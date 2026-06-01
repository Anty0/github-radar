#!/usr/bin/env python3
"""GitHub Radar — daily GitHub digest.

Talks to api.github.com over plain HTTPS using a PAT in the GH_TOKEN env var.
Emits a JSON document on stdout with the day's categorised data:

  - viewer: the authenticated login (discovered at runtime)
  - orgs: organisations the viewer belongs to (discovered at runtime, alphabetic)
  - solo_scopes: the viewer's personal namespace plus any orgs where they are the
    sole member; items in these scopes are treated as implicitly assigned to the
    viewer (the user doesn't use the assignees field there because there's nobody
    else to assign to) and are excluded from "unassigned"-flavoured sections.
  - sections (each grouped by repo, orgs sorted before personal repos):
      - assigned: issues/PRs assigned to me, plus everything open in solo scopes
      - authored: issues/PRs I authored
      - review_requested: PRs waiting for my review
      - new_unattended: items <14d old, no assignee/reviewer, no comments
                       (solo scopes are excluded — nothing is "unassigned" there)
      - new_discussions: discussions <14d old with zero comments
      - threads_waiting: issues/PRs/discussions I commented on, with newer non-bot activity in any thread
      - mentions_unanswered: items where someone @-mentioned me and I haven't replied since
      - stale_closed: items I was involved in, closed by a bot in the last 7d
      - recent_merges_uninvolved: PRs merged in the last 7d in repos I care about, with no involvement (excluding "review requested but never reviewed"; solo scopes excluded — by definition nobody else is involved there)

This script does NOT produce critical/top_picks — those are the agent's job at render time.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

POOL = ThreadPoolExecutor(max_workers=16)

TOKEN = os.environ.get("GH_TOKEN", "")
if not TOKEN:
    print(json.dumps({"error": "GH_TOKEN not set"}), file=sys.stderr)
    sys.exit(2)

NOW = datetime.now(timezone.utc)
TWO_WEEKS_AGO = (NOW - timedelta(days=14)).strftime("%Y-%m-%d")
SEVEN_DAYS_AGO_ISO = (NOW - timedelta(days=7))
SEVEN_DAYS_AGO = SEVEN_DAYS_AGO_ISO.strftime("%Y-%m-%d")
THIRTY_DAYS_AGO = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
SIXTY_DAYS_AGO = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")

# Extra orgs to fold into ORGS that the token cannot enumerate on its own —
# e.g. orgs where your membership is private and visible only to a classic
# read:org token. Comma-separated, e.g. EXTRA_ORGS="acme,Erasmus2016".
# Left empty by default; data reads still work for any public repo within them.
EXTRA_ORGS = [o.strip() for o in os.environ.get("EXTRA_ORGS", "").split(",") if o.strip()]

# Populated by discover_identity() at the start of main().
VIEWER = ""
ORGS = []
# Scopes treated as "implicitly assigned to the viewer": the viewer's personal
# namespace plus any orgs where they are the sole member. Populated alongside
# ORGS by discover_identity().
SOLO_SCOPES = []

HEADERS = {
    "Authorization": f"bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "github-radar",
}


def _req(url, data=None, method=None):
    """Low-level HTTP with retry on rate limit / 5xx."""
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            for k, v in HEADERS.items():
                req.add_header(k, v)
            if data is not None and "Content-Type" not in dict(req.header_items()):
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (403, 429) and "rate limit" in body.lower():
                reset = int(e.headers.get("X-RateLimit-Reset", "0") or "0")
                wait = max(5, min(60, reset - int(time.time())))
                time.sleep(wait)
                continue
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {"_error": f"HTTP {e.code}: {body[:300]}", "_url": url}
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {"_error": str(e), "_url": url}
    return {"_error": "exhausted retries", "_url": url}


def gql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    return _req("https://api.github.com/graphql", data=payload, method="POST")


def _has_type_qualifier(q):
    """True if the query already constrains result type (issue vs PR)."""
    ql = q.lower()
    return any(t in ql for t in ("is:issue", "is:pr", "is:pull-request",
                                 "type:issue", "type:pr", "type:pull-request"))


def _search_issues_single(q, limit=200):
    """REST search/issues with pagination; returns list of items (one query)."""
    out = []
    page = 1
    while True:
        params = urllib.parse.urlencode({"q": q, "per_page": 100, "page": page})
        d = _req(f"https://api.github.com/search/issues?{params}")
        if "_error" in d:
            return {"_error": d["_error"]}
        items = d.get("items", []) or []
        out.extend(items)
        if len(items) < 100 or len(out) >= limit:
            break
        page += 1
    return out[:limit]


def search_issues(q, limit=200):
    """REST search/issues, returning both issues and PRs.

    GitHub's search API now rejects any query that doesn't constrain the result
    type ("Query must include 'is:issue' or 'is:pull-request'"), and a single
    query can only return one type. To keep callers type-agnostic, a query with
    no type qualifier is run once per type and the results are merged; queries
    that already specify a type pass through unchanged.
    """
    if _has_type_qualifier(q):
        return _search_issues_single(q, limit=limit)
    out = []
    seen = set()
    for t in ("is:issue", "is:pull-request"):
        part = _search_issues_single(f"{q} {t}", limit=limit)
        if isinstance(part, dict):  # propagate first error
            return part
        for it in part:
            key = (it.get("repository_url"), it.get("number"))
            if key not in seen:
                seen.add(key)
                out.append(it)
    return out[:limit]


# ---------- identity ----------

def _org_member_count_le2(login):
    """Best-effort member count (capped at 2) via REST.

    Returns an int (1 or 2) when the token can list the org's members, or None
    when that listing is forbidden/unavailable. Kept separate from org
    enumeration so a permission failure here can never drop an org from ORGS.
    """
    d = _req(f"https://api.github.com/orgs/{login}/members?per_page=2")
    if isinstance(d, list):
        return len(d)
    return None  # forbidden / error → caller treats as "not solo"


def discover_identity():
    """Fetch viewer login, organisations, and sole-member orgs dynamically.

    Sets globals VIEWER, ORGS, SOLO_SCOPES.

    Org enumeration deliberately requests ONLY the org login — no
    membersWithRole sub-field. A fine-grained token scoped to one org gets a
    FORBIDDEN error on membersWithRole for *other* orgs, and GraphQL responds by
    nulling the entire org node, which would otherwise silently drop that org
    (e.g. tolgee) from the report. EXTRA_ORGS is unioned in afterwards for orgs
    the token can't enumerate at all (private membership).

    Solo detection runs as a separate, error-tolerant REST pass: an org counts
    as "solo" when it has exactly one visible member (just the viewer). The
    viewer's personal namespace is always solo. Orgs whose membership can't be
    read default to "not solo" — conservative, since we'd rather treat a
    multi-member org as needing explicit assignment than the other way round.
    """
    global VIEWER, ORGS, SOLO_SCOPES
    d = gql("""query {
      viewer {
        login
        organizations(first:100) {
          nodes { login }
        }
      }
    }""")
    if "_error" in d:
        raise RuntimeError(f"Cannot fetch viewer identity: {d.get('_error')}")
    v = d.get("data", {}).get("viewer", {}) or {}
    VIEWER = v.get("login") or ""
    if not VIEWER:
        raise RuntimeError("Empty viewer login")
    nodes = (v.get("organizations", {}) or {}).get("nodes", []) or []

    orgs = []
    seen = set()
    for n in nodes:
        login = n.get("login") if n else None
        if login and login not in seen:
            seen.add(login)
            orgs.append(login)
    for login in EXTRA_ORGS:  # orgs the token can't enumerate (private membership)
        if login not in seen:
            seen.add(login)
            orgs.append(login)

    solo = {VIEWER}  # personal namespace is always solo
    for login in orgs:
        if _org_member_count_le2(login) == 1:
            solo.add(login)

    ORGS = sorted(orgs, key=str.lower)
    SOLO_SCOPES = sorted(solo, key=str.lower)


# ---------- helpers ----------

def repo_of(item):
    if "repository_url" in item:
        parts = item["repository_url"].rsplit("/", 2)
        return f"{parts[-2]}/{parts[-1]}"
    return item.get("repo")


def is_bot(login):
    if not login:
        return False
    l = login.lower()
    return l.endswith("[bot]") or l in {"github-actions", "stale", "stale-bot", "renovate", "dependabot", "coderabbitai"}


def labels_of(item):
    return [l["name"].lower() for l in item.get("labels", [])] if isinstance(item.get("labels"), list) else []


def to_obj(item, kind=None):
    """Normalise REST search item -> dict for the report."""
    is_pr = "pull_request" in item
    return {
        "repo": repo_of(item),
        "number": item["number"],
        "title": item["title"],
        "url": item["html_url"],
        "kind": kind or ("pr" if is_pr else "issue"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "closed_at": item.get("closed_at"),
        "state": item.get("state"),
        "author": (item.get("user") or {}).get("login"),
        "assignees": [a["login"] for a in item.get("assignees") or []],
        "labels": labels_of(item),
        "comments": item.get("comments", 0),
        "draft": item.get("draft", False),
    }


def order_repos(repo_names):
    """Sort: org repos first in ORGS order, then viewer's own (VIEWER/*) alphabetically, then anything else."""
    def key(r):
        owner = r.split("/", 1)[0]
        if owner in ORGS:
            return (0, ORGS.index(owner), r.lower())
        if owner == VIEWER:
            return (1, 0, r.lower())
        return (2, 0, r.lower())
    return sorted(repo_names, key=key)


def group_by_repo(items):
    g = {}
    for it in items:
        g.setdefault(it["repo"], []).append(it)
    return [{"repo": r, "items": g[r]} for r in order_repos(g.keys())]


# ---------- section queries ----------

def section_assigned():
    """Open issues/PRs assigned to me, plus everything open in solo scopes.

    Items in personal repos and sole-member orgs are treated as implicitly
    assigned to the viewer (since there's nobody else to assign), even when
    no GitHub `assignees` field is set.
    """
    items = search_issues(f"is:open assignee:{VIEWER}")
    if isinstance(items, dict):
        return items
    by_key = {(repo_of(i), i["number"]): i for i in items}
    for scope in SOLO_SCOPES:
        qualifier = "user" if scope == VIEWER else "org"
        more = search_issues(f"is:open {qualifier}:{scope}", limit=300)
        if isinstance(more, dict):
            continue
        for it in more:
            by_key.setdefault((repo_of(it), it["number"]), it)
    return group_by_repo([to_obj(i) for i in by_key.values()])


def section_authored():
    items = search_issues(f"is:open author:{VIEWER}")
    if isinstance(items, dict): return items
    return group_by_repo([to_obj(i) for i in items])


def section_review_requested():
    items = search_issues(f"is:open is:pr review-requested:{VIEWER}")
    if isinstance(items, dict): return items
    return group_by_repo([to_obj(i) for i in items])


def section_new_unattended():
    """Issues/PRs created < 14d ago, no assignee, no reviewer, no comments.

    Solo scopes (personal repos, sole-member orgs) are skipped because the
    viewer doesn't use assignees there — items would always look "unattended"
    but they're already implicitly the viewer's via `section_assigned`.
    """
    out = []
    scopes = [f"org:{o}" for o in ORGS if o not in SOLO_SCOPES]
    raw = []
    for scope in scopes:
        q = f"is:open no:assignee comments:0 created:>={TWO_WEEKS_AGO} {scope}"
        items = search_issues(q, limit=100)
        if isinstance(items, dict):
            continue
        for it in items:
            obj = to_obj(it)
            if obj["author"] == VIEWER:
                continue
            raw.append((it, obj))
    # For PRs, check requested reviewers in parallel.
    pr_jobs = {}
    for it, obj in raw:
        if obj["kind"] == "pr":
            pr_jobs[(obj["repo"], obj["number"])] = POOL.submit(_req, it["pull_request"]["url"])
    pr_results = {k: f.result() for k, f in pr_jobs.items()}
    for it, obj in raw:
        if obj["kind"] == "pr":
            pr = pr_results.get((obj["repo"], obj["number"])) or {}
            if pr.get("requested_reviewers") or pr.get("requested_teams"):
                continue
        out.append(obj)
    return group_by_repo(out)


def section_new_discussions():
    """Discussions <14d old with no comments. Search via GraphQL per org."""
    out = []
    scopes = [f"org:{o}" for o in ORGS] + [f"user:{VIEWER}"]
    for s in scopes:
        q = f"{s} created:>={TWO_WEEKS_AGO}"
        d = gql(
            """query($q:String!) {
              search(query:$q, type:DISCUSSION, first:50) {
                nodes {
                  ... on Discussion {
                    number title url createdAt updatedAt
                    author { login }
                    repository { nameWithOwner }
                    comments { totalCount }
                  }
                }
              }
            }""",
            {"q": q},
        )
        if "_error" in d or "errors" in d:
            continue
        for n in (d.get("data", {}).get("search", {}) or {}).get("nodes", []) or []:
            if not n: continue
            if (n.get("comments") or {}).get("totalCount", 0) > 0:
                continue
            if (n.get("author") or {}).get("login") == VIEWER:
                continue
            out.append({
                "repo": n["repository"]["nameWithOwner"],
                "number": n["number"],
                "title": n["title"],
                "url": n["url"],
                "kind": "discussion",
                "created_at": n["createdAt"],
                "updated_at": n["updatedAt"],
                "author": (n.get("author") or {}).get("login"),
                "labels": [],
                "comments": 0,
            })
    return group_by_repo(out)


# Cached per-PR review threads
def fetch_pr_review_threads(owner, name, number):
    out = []
    cursor = None
    for _ in range(5):  # cap at 5 pages
        d = gql(
            """query($o:String!,$n:String!,$num:Int!,$c:String){
              repository(owner:$o, name:$n){ pullRequest(number:$num){
                reviewThreads(first:50, after:$c){
                  pageInfo{ hasNextPage endCursor }
                  nodes{ id isResolved
                    comments(first:50){ nodes{ author{login} createdAt body } }
                  } } } } }""",
            {"o": owner, "n": name, "num": number, "c": cursor},
        )
        if "_error" in d or "errors" in d:
            return []
        rt = (((d.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}
        for n in rt.get("nodes") or []:
            out.append(n)
        if not rt.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = rt["pageInfo"]["endCursor"]
    return out


def fetch_issue_comments(owner, name, number, is_pr):
    """All top-level comments on an issue or PR."""
    out = []
    page = 1
    while True:
        d = _req(
            f"https://api.github.com/repos/{owner}/{name}/issues/{number}/comments?per_page=100&page={page}"
        )
        if isinstance(d, dict) and d.get("_error"):
            return out
        if not d:
            break
        out.extend(d)
        if len(d) < 100:
            break
        page += 1
        if page > 5:
            break
    return out


def fetch_discussion_comments(owner, name, number):
    """All comments + replies for a discussion."""
    d = gql(
        """query($o:String!,$n:String!,$num:Int!){
          repository(owner:$o,name:$n){ discussion(number:$num){
            comments(first:50){ nodes{
              author{login} createdAt
              replies(first:50){ nodes{ author{login} createdAt } }
            } } } } }""",
        {"o": owner, "n": name, "num": number},
    )
    if "_error" in d or "errors" in d:
        return []
    return ((((d.get("data") or {}).get("repository") or {}).get("discussion") or {}).get("comments") or {}).get("nodes") or []


def thread_needs_response(comments, mention_only=False):
    """Given an ordered list of comments [{author, created_at}], decide if I should respond.

    A thread is "waiting on me" if:
      - I have at least one comment in it (commenter case) OR I'm mentioned (mention case), AND
      - There exists a non-bot, non-me comment chronologically after my last comment (or after the mention if I never commented).
    """
    my_times = [c["createdAt"] for c in comments if (c.get("author") or {}).get("login") == VIEWER]
    if mention_only and not my_times:
        # In mention-only mode, "after the mention" needs the mention timestamp from caller, so just check there is any later non-bot non-me.
        return any(not is_bot((c.get("author") or {}).get("login")) and (c.get("author") or {}).get("login") != VIEWER for c in comments)
    if not my_times:
        return False
    last_me = max(my_times)
    for c in comments:
        login = (c.get("author") or {}).get("login")
        if not login or login == VIEWER or is_bot(login):
            continue
        if c["createdAt"] > last_me:
            return True
    return False


def _has_atmention(text, viewer):
    """Return True if `text` contains an @-mention of `viewer` (case-insensitive).

    GitHub usernames are alnum + hyphens. We require a non-username boundary on
    both sides of `@viewer` so that `email@viewer.com` and `@viewer-extra` don't
    match.
    """
    if not text or not viewer:
        return False
    pattern = r'(?<![A-Za-z0-9_])@' + re.escape(viewer) + r'(?![A-Za-z0-9-])'
    return re.search(pattern, text, re.IGNORECASE) is not None


def _ev_login(e):
    """Author login from either a flat-author dict ({author: 'name'}) or nested ({author: {login}})."""
    a = e.get("author")
    if isinstance(a, dict):
        return a.get("login")
    return a


def _ev_time(e):
    return e.get("createdAt") or e.get("created_at")


def find_unanswered_mention(events, viewer):
    """Given a chrono list of events with `author`, `createdAt`/`created_at`, `body`,
    decide whether there's an @-mention of `viewer` (by a non-viewer, non-bot author)
    that `viewer` has not replied to.

    Returns True if the latest such mention happens strictly before `viewer`'s last
    event in the list (or `viewer` has no events at all after it).
    """
    t_mention = None
    for e in events:
        author = _ev_login(e)
        if not author or author == viewer or is_bot(author):
            continue
        if _has_atmention(e.get("body") or "", viewer):
            t = _ev_time(e)
            if t and (t_mention is None or t > t_mention):
                t_mention = t
    if t_mention is None:
        return False
    for e in events:
        if _ev_login(e) != viewer:
            continue
        t = _ev_time(e)
        if t and t > t_mention:
            return False
    return True


def _analyze_thread_candidate(it, mention_mode=False):
    owner, name = repo_of(it).split("/")
    number = it["number"]
    is_pr = "pull_request" in it
    threads_waiting_count = 0

    comments = fetch_issue_comments(owner, name, number, is_pr)
    norm = [{"author": {"login": (c.get("user") or {}).get("login")}, "createdAt": c["created_at"], "body": c.get("body") or ""} for c in comments]
    if mention_mode:
        # Include the issue/PR body itself as a possible source of the @-mention
        # (GitHub's `mentions:` search qualifier also matches body mentions).
        events = list(norm)
        body_author = (it.get("user") or {}).get("login")
        if body_author and it.get("body"):
            events.insert(0, {
                "author": {"login": body_author},
                "createdAt": it.get("created_at"),
                "body": it.get("body") or "",
            })
        if find_unanswered_mention(events, VIEWER):
            threads_waiting_count += 1
    else:
        if thread_needs_response(norm):
            threads_waiting_count += 1

    if is_pr:
        for rt in fetch_pr_review_threads(owner, name, number):
            if rt.get("isResolved"):
                continue
            tc = rt.get("comments", {}).get("nodes", []) or []
            if mention_mode:
                if find_unanswered_mention(tc, VIEWER):
                    threads_waiting_count += 1
            else:
                if thread_needs_response(tc):
                    threads_waiting_count += 1
    if threads_waiting_count > 0:
        obj = to_obj(it)
        obj["threads_waiting"] = threads_waiting_count
        return obj
    return None


def section_threads_waiting():
    """Issues/PRs/discussions I commented on; per-thread check."""
    items = search_issues(f"is:open commenter:{VIEWER} updated:>={SIXTY_DAYS_AGO}", limit=200)
    if isinstance(items, dict): return items
    out = []
    futs = [POOL.submit(_analyze_thread_candidate, it, False) for it in items]
    for f in as_completed(futs):
        r = f.result()
        if r: out.append(r)

    # Discussions: search for ones I commented on
    disc = gql(
        """query($q:String!) {
          search(query:$q, type:DISCUSSION, first:50) {
            nodes { ... on Discussion {
              number title url createdAt updatedAt
              repository { nameWithOwner }
              author { login }
            } }
          } }""",
        {"q": f"commenter:{VIEWER} updated:>={SIXTY_DAYS_AGO}"},
    )
    if "errors" not in disc and "_error" not in disc:
        for n in (disc.get("data", {}).get("search", {}) or {}).get("nodes", []) or []:
            if not n: continue
            owner, name = n["repository"]["nameWithOwner"].split("/")
            top_comments = fetch_discussion_comments(owner, name, n["number"])
            waiting = 0
            # Discussion has many threads: each top-level comment + its replies is one thread.
            for tc in top_comments:
                # Build chrono list of (author, time) including the parent
                chrono = [{"author": tc.get("author"), "createdAt": tc["createdAt"]}]
                for r in (tc.get("replies") or {}).get("nodes") or []:
                    chrono.append({"author": r.get("author"), "createdAt": r["createdAt"]})
                if thread_needs_response(chrono):
                    waiting += 1
            if waiting:
                out.append({
                    "repo": n["repository"]["nameWithOwner"],
                    "number": n["number"],
                    "title": n["title"],
                    "url": n["url"],
                    "kind": "discussion",
                    "author": (n.get("author") or {}).get("login"),
                    "created_at": n["createdAt"],
                    "updated_at": n["updatedAt"],
                    "labels": [],
                    "threads_waiting": waiting,
                })

    return group_by_repo(out)


def section_mentions_unanswered():
    """Items where I'm mentioned and any thread is waiting on my response."""
    items = search_issues(f"is:open mentions:{VIEWER} updated:>={SIXTY_DAYS_AGO}", limit=200)
    if isinstance(items, dict): return items
    # Skip items I authored (they're in "authored")
    items = [it for it in items if (it.get("user") or {}).get("login") != VIEWER]
    out = []
    futs = [POOL.submit(_analyze_thread_candidate, it, True) for it in items]
    for f in as_completed(futs):
        r = f.result()
        if r: out.append(r)
    return group_by_repo(out)


def _closer_of(it):
    owner, name = repo_of(it).split("/")
    number = it["number"]
    evs = _req(f"https://api.github.com/repos/{owner}/{name}/issues/{number}/events?per_page=100")
    if isinstance(evs, dict) and evs.get("_error"):
        return None
    closer = None
    for ev in evs or []:
        if ev.get("event") == "closed":
            closer = (ev.get("actor") or {}).get("login")
    if closer and is_bot(closer):
        obj = to_obj(it)
        obj["closed_by"] = closer
        return obj
    return None


def section_stale_closed():
    """Issues/PRs I'm involved in, closed in last 7d by a bot."""
    items = search_issues(f"involves:{VIEWER} closed:>={SEVEN_DAYS_AGO}", limit=200)
    if isinstance(items, dict): return items
    out = []
    for r in (f.result() for f in as_completed([POOL.submit(_closer_of, it) for it in items])):
        if r: out.append(r)
    return group_by_repo(out)


def section_recent_merges_uninvolved():
    """PRs merged in last 7d in repos I care about, where I had zero involvement.

    Per the spec, "review requested but never reviewed" does NOT count as involvement.
    Solo scopes are skipped — by definition there's nobody else to be involved.
    """
    out = []
    # Use search; the qualifier `-involves:` treats review-requested as involvement, so we add it back via post-filter.
    # Strategy per scope: search merged:>=7d -author:me -assignee:me -commenter:me -mentions:me
    scopes = [f"org:{o}" for o in ORGS if o not in SOLO_SCOPES]
    for scope in scopes:
        q = (
            f"is:pr is:merged merged:>={SEVEN_DAYS_AGO} {scope} "
            f"-author:{VIEWER} -assignee:{VIEWER} -commenter:{VIEWER} -mentions:{VIEWER}"
        )
        items = search_issues(q, limit=200)
        if isinstance(items, dict):
            continue
        for it in items:
            obj = to_obj(it)
            # Heuristic: include even if "review-requested:me" set, since spec said review-request alone isn't involvement.
            out.append(obj)
    return group_by_repo(out)


def main():
    discover_identity()
    print(f"  viewer: {VIEWER}", file=sys.stderr)
    print(f"  orgs:   {', '.join(ORGS) or '(none)'}", file=sys.stderr)
    print(f"  solo:   {', '.join(SOLO_SCOPES) or '(none)'}", file=sys.stderr)

    section_fns = {
        "assigned": section_assigned,
        "authored": section_authored,
        "review_requested": section_review_requested,
        "new_unattended": section_new_unattended,
        "new_discussions": section_new_discussions,
        "threads_waiting": section_threads_waiting,
        "mentions_unanswered": section_mentions_unanswered,
        "stale_closed": section_stale_closed,
        "recent_merges_uninvolved": section_recent_merges_uninvolved,
    }
    futs = {k: POOL.submit(fn) for k, fn in section_fns.items()}
    sections = {}
    for k, f in futs.items():
        try:
            t0 = time.time()
            sections[k] = f.result()
            print(f"  section {k}: {time.time()-t0:.1f}s", file=sys.stderr)
        except Exception as e:
            sections[k] = {"_error": f"{type(e).__name__}: {e}"}
    report = {
        "generated_at": NOW.isoformat(),
        "viewer": VIEWER,
        "orgs": ORGS,
        "solo_scopes": SOLO_SCOPES,
        "windows": {
            "two_weeks_ago": TWO_WEEKS_AGO,
            "seven_days_ago": SEVEN_DAYS_AGO,
            "sixty_days_ago": SIXTY_DAYS_AGO,
        },
        "sections": sections,
    }
    json.dump(report, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
