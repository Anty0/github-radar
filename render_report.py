#!/usr/bin/env python3
"""Render gh_report.py JSON into a self-contained HTML artifact for Cowork.

Usage:
    python3 render_report.py [picks.json] < report.json > report.html

The optional picks.json is supplied by the daily agent after it has read
report.json and reasoned about what's important. Expected shape:

    {
      "critical": [
        { "repo": "...", "number": 123, "reason": "why this is critical",
          "title": "optional override", "url": "optional override",
          "kind": "issue|pr|discussion" }, ...
      ],
      "top_picks": [
        { "repo": "...", "number": 456, "reason": "why this is a good pick today" }, ...
      ],
      "agent_summary": "optional one-line narrative shown under the header"
    }

If the picks file is missing or empty, the Critical and Top picks sections
render as "Nothing flagged today." — never auto-populated.
"""
import json
import html
import sys
from datetime import datetime, timezone


SECTION_META = [
    ("critical", "Critical / urgent",                  "items the daily agent flagged as critical for today"),
    ("top_picks", "Top picks for today",               "1–3 things the daily agent chose for you to tackle today"),
    ("assigned", "Assigned to you",                    "open issues / PRs where you're an assignee"),
    ("authored", "Authored by you",                    "open issues / PRs you opened"),
    ("review_requested", "Reviews requested",          "open PRs waiting for your review"),
    ("threads_waiting", "Threads waiting on you",      "items where you commented and someone has replied since"),
    ("mentions_unanswered", "Mentions, no reply yet",  "items where you were @-mentioned and haven't replied"),
    ("new_unattended", "New & unattended",             "items <14 days old, no assignee/reviewer, no comments"),
    ("new_discussions", "New discussions (no replies)","discussions <14 days old with zero comments"),
    ("stale_closed", "Closed by bots in last 7d",      "items you were involved in, closed automatically — review if anything matters"),
    ("recent_merges_uninvolved", "Recently merged without you", "PRs merged in last 7d in repos you care about, where you had no involvement"),
]


def esc(s):
    return html.escape(str(s) if s is not None else "")


def kind_badge(kind):
    if kind == "pr":   return '<span class="kind pr">PR</span>'
    if kind == "discussion": return '<span class="kind disc">Disc</span>'
    return '<span class="kind issue">Issue</span>'


def label_chips(labels):
    return "".join(f'<span class="lbl">{esc(l)}</span>' for l in (labels or [])[:6])


DERIVED_SECTIONS = {"critical", "top_picks"}


def render_item(it, section_key="", pick_rank=None, show_reason=False, show_closed_by=False, show_threads=False):
    title = esc(it.get("title", ""))
    url = esc(it.get("url", ""))
    repo = esc(it.get("repo", ""))
    num = esc(it.get("number", ""))
    author = esc(it.get("author") or "?")
    owner_attr = esc(_owner_of(it))
    updated = esc(it.get("updated_at") or "")
    item_key = f'{repo}#{num}'
    item_attrs = [
        f'data-org="{owner_attr}"',
        f'data-key="{esc(item_key)}"',
    ]
    if section_key:
        item_attrs.append(f'data-section="{esc(section_key)}"')
    if updated:
        item_attrs.append(f'data-updated="{updated}"')
    if pick_rank is not None:
        item_attrs.append(f'data-pick-rank="{int(pick_rank)}"')
    parts = [f'<div class="item" {" ".join(item_attrs)}>']
    parts.append(f'<div class="row1">{kind_badge(it.get("kind","issue"))}')
    parts.append(f'  <a href="{url}" target="_blank" rel="noopener" class="title">{title}</a>')
    parts.append(f'  <span class="ref">{repo}#{num}</span>')
    parts.append(f'  <span class="dismissed-tag">dismissed</span>')
    # Derived sections (critical, top_picks) have no own dismiss button —
    # they inherit dismissed-ness from the item's "real" section. Dismiss
    # the underlying item in its native section to hide it from picks.
    if section_key and updated and section_key not in DERIVED_SECTIONS:
        parts.append(f'  <button class="dismiss-btn" type="button" title="Dismiss until any GitHub update">✕</button>')
    parts.append(f'</div>')
    extras = []
    if it.get("author"):
        extras.append(f'by {author}')
    if it.get("assignees"):
        extras.append(f'assignees: {esc(", ".join(it["assignees"]))}')
    if show_threads and it.get("threads_waiting"):
        extras.append(f'<span class="warn">{it["threads_waiting"]} thread(s) waiting</span>')
    if show_closed_by and it.get("closed_by"):
        extras.append(f'<span class="warn">closed by {esc(it["closed_by"])}</span>')
    if show_reason and it.get("_reason"):
        extras.append(f'<span class="reason">{esc(it["_reason"])}</span>')
    if it.get("created_at"):
        extras.append(f'opened {esc(it["created_at"][:10])}')
    parts.append(f'<div class="row2">{" · ".join(extras)}</div>')
    if it.get("labels"):
        parts.append(f'<div class="row3">{label_chips(it["labels"])}</div>')
    parts.append('</div>')
    return "\n".join(parts)


def render_repo_group(grp, section_key="", **kw):
    repo = esc(grp["repo"])
    owner_attr = esc(_owner_of(grp))
    items_html = "\n".join(render_item(it, section_key=section_key, **kw) for it in grp["items"])
    return f'<div class="repo" data-org="{owner_attr}"><div class="repo-name">{repo} <span class="count">({len(grp["items"])})</span></div>{items_html}</div>'


def render_section(key, data, title, hint):
    if isinstance(data, dict) and data.get("_error"):
        body = f'<div class="empty err">Error: {esc(data["_error"])}</div>'
        count = "!"
    elif key in ("critical", "top_picks"):
        items = data or []
        if not items:
            body = '<div class="empty">Nothing here.</div>'
        else:
            # Multi-candidate: the agent emits up to ~6 ordered candidates;
            # JS shows the top 3 non-dismissed. Each gets a 1-based rank so
            # the visibility cap is stable.
            body = "\n".join(
                render_item(it, section_key=key, pick_rank=rank, show_reason=(key == "top_picks"), show_threads=True, show_closed_by=True)
                for rank, it in enumerate(items, start=1)
            )
        count = len(items)
    else:
        grps = data or []
        total = sum(len(g["items"]) for g in grps if isinstance(g, dict))
        if not total:
            body = '<div class="empty">Nothing here.</div>'
        else:
            body = "\n".join(
                render_repo_group(g,
                                  section_key=key,
                                  show_threads=(key in ("threads_waiting", "mentions_unanswered")),
                                  show_closed_by=(key == "stale_closed"))
                for g in grps if isinstance(g, dict)
            )
        count = total
    open_attr = " open" if key in ("critical", "top_picks") or (isinstance(count, int) and count > 0 and key in ("review_requested", "threads_waiting", "mentions_unanswered", "new_unattended", "new_discussions")) else ""
    return f"""
<details class="section" id="sec-{key}"{open_attr}>
  <summary><span class="sec-title">{esc(title)}</span> <span class="sec-count">{esc(count)}</span></summary>
  <div class="sec-hint">{esc(hint)}</div>
  <div class="filter-empty" style="display:none;">No items for this organization in this section.</div>
  {body}
</details>
"""


def _index_items(report):
    """Build {(repo, number) -> item dict} across every section, so agent picks
    can be supplied with just (repo, number, reason) and we fill in the rest."""
    idx = {}
    for grp_list in (report.get("sections") or {}).values():
        if not isinstance(grp_list, list):
            continue
        for grp in grp_list:
            if not isinstance(grp, dict):
                continue
            for it in grp.get("items", []) or []:
                key = (it.get("repo"), it.get("number"))
                if key not in idx:
                    idx[key] = it
    return idx


def _resolve_pick(pick, item_idx):
    """Merge agent-supplied minimal pick with the full item from the report."""
    if not isinstance(pick, dict):
        return None
    repo = pick.get("repo")
    number = pick.get("number")
    base = item_idx.get((repo, number)) or {}
    merged = {**base, **{k: v for k, v in pick.items() if v is not None}}
    # Normalise the reason field — accept either `reason` or `_reason`
    if "reason" in merged and "_reason" not in merged:
        merged["_reason"] = merged["reason"]
    # Bare minimum so the item still renders if the agent supplied an unknown item
    merged.setdefault("kind", "issue")
    merged.setdefault("title", pick.get("title", f"{repo}#{number}"))
    merged.setdefault("repo", repo)
    merged.setdefault("number", number)
    merged.setdefault("url", pick.get("url") or "")
    return merged


def _owner_of(item_or_grp):
    """Return the owner login from an item dict or repo group dict."""
    repo = (item_or_grp or {}).get("repo") or ""
    return repo.split("/", 1)[0] if "/" in repo else repo


def _compute_org_counts(critical, top_picks, sections_data, viewer):
    """Walk every item that will be rendered and return:
      - sec_counts: {section_key: {owner: count, "_all": total}}
      - org_totals: {owner: unique_item_count, "_all": total_unique}
      - ordered_owners: [owner, ...] alphabetic, with the viewer's own login last
    Counts inside each section are simple sums; org totals are de-duped by
    (repo, number, kind) so an item appearing in multiple sections only adds
    one to its owner's button count.
    """
    sec_counts = {}
    items_per_owner = {}  # owner -> set of (repo, number, kind)

    def process(key, items_iter):
        by_owner = {"_all": 0}
        for it in items_iter or []:
            if not isinstance(it, dict):
                continue
            owner = _owner_of(it) or "(unknown)"
            by_owner[owner] = by_owner.get(owner, 0) + 1
            by_owner["_all"] += 1
            items_per_owner.setdefault(owner, set()).add(
                (it.get("repo"), it.get("number"), it.get("kind"))
            )
        sec_counts[key] = by_owner

    process("critical", critical)
    process("top_picks", top_picks)
    for key in ("assigned", "authored", "review_requested", "threads_waiting",
                "mentions_unanswered", "new_unattended", "new_discussions",
                "stale_closed", "recent_merges_uninvolved"):
        grps = sections_data.get(key) or []
        flat = []
        if isinstance(grps, list):
            for g in grps:
                if isinstance(g, dict):
                    flat.extend(g.get("items") or [])
        process(key, flat)

    all_unique = set()
    for s in items_per_owner.values():
        all_unique |= s
    org_totals = {"_all": len(all_unique)}
    for owner, items in items_per_owner.items():
        org_totals[owner] = len(items)

    others = sorted([o for o in items_per_owner if o != viewer], key=str.lower)
    ordered_owners = others + ([viewer] if viewer in items_per_owner else [])
    return sec_counts, org_totals, ordered_owners


def render(report, picks=None):
    picks = picks or {}
    item_idx = _index_items(report)
    critical = [p for p in (_resolve_pick(p, item_idx) for p in picks.get("critical") or []) if p]
    top_picks = [p for p in (_resolve_pick(p, item_idx) for p in picks.get("top_picks") or []) if p]
    agent_summary = picks.get("agent_summary")

    gen = report.get("generated_at", "")
    try:
        dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
        gen_human = dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        gen_human = gen

    viewer_raw = report.get("viewer", "") or ""
    viewer = esc(viewer_raw)
    orgs = ", ".join(report.get("orgs", []))
    sections_data = report.get("sections", {}) or {}

    sec_counts, org_totals, ordered_owners = _compute_org_counts(
        critical, top_picks, sections_data, viewer_raw
    )

    section_blocks = []
    for key, title, hint in SECTION_META:
        if key == "critical":
            data = critical
        elif key == "top_picks":
            data = top_picks
        else:
            data = sections_data.get(key)
        section_blocks.append(render_section(key, data, title, hint))

    body = "\n".join(section_blocks)

    # Filter bar: "All" plus one chip per owner (viewer-owned repos labeled "Personal").
    if ordered_owners:
        btns = [
            f'<button class="filter-btn active" type="button" data-org="_all">'
            f'All <span class="cnt">{org_totals.get("_all", 0)}</span></button>'
        ]
        for owner in ordered_owners:
            label = "Personal" if owner == viewer_raw else owner
            btns.append(
                f'<button class="filter-btn" type="button" data-org="{esc(owner)}">'
                f'{esc(label)} <span class="cnt">{org_totals.get(owner, 0)}</span></button>'
            )
        filter_bar_html = f'<div class="filter-bar">{"".join(btns)}</div>'
    else:
        filter_bar_html = ""

    # sec_counts was previously injected as a JS constant so the org-filter
    # could update badges without walking the DOM. With dismissals in the
    # mix the badges have to be derived from the live DOM anyway, so we
    # drop the constant and rely on the server-rendered initial counts as
    # the first paint.
    _ = sec_counts  # noqa: F841 — kept for future use

    style = """
:root {
  color-scheme: dark;
  --fg: #e6edf3;
  --fg-muted: #8b949e;
  --border-strong: #30363d;
  --border-soft: #21262d;
  --surface: #161b22;
  --surface-hover: #1c2128;
  --chip-bg: #21262d;
  --chip-fg: #e6edf3;
  --link: #58a6ff;
  --btn-bg: #21262d;
  --btn-bg-hover: #30363d;
  --btn-border: #30363d;
  --badge-pr-bg: rgba(56,139,253,0.18);
  --badge-pr-fg: #79c0ff;
  --badge-issue-bg: rgba(63,185,80,0.18);
  --badge-issue-fg: #56d364;
  --badge-disc-bg: rgba(187,128,9,0.20);
  --badge-disc-fg: #e3b341;
  --warn: #ff8e6b;
  --reason: #d2a8ff;
  --error: #ff7b72;
  --summary-bg: rgba(187,128,9,0.15);
  --summary-fg: #f0d77c;
  --summary-border: #d4a017;
}
:root[data-theme="light"] {
  color-scheme: light;
  --fg: #1f2328;
  --fg-muted: #57606a;
  --border-strong: #d0d7de;
  --border-soft: #eaeef2;
  --surface: #f6f8fa;
  --surface-hover: #f6f8fa;
  --chip-bg: #eaeef2;
  --chip-fg: #1f2328;
  --link: #0969da;
  --btn-bg: #ffffff;
  --btn-bg-hover: #f6f8fa;
  --btn-border: #d0d7de;
  --badge-pr-bg: #ddf4ff;
  --badge-pr-fg: #0550ae;
  --badge-issue-bg: #dafbe1;
  --badge-issue-fg: #1a7f37;
  --badge-disc-bg: #fff8c5;
  --badge-disc-fg: #7d4e00;
  --warn: #9a3412;
  --reason: #6e40c9;
  --error: #cf222e;
  --summary-bg: #fff8c5;
  --summary-fg: #4d3a00;
  --summary-border: #d4a017;
}
* { box-sizing: border-box; }
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       color: var(--fg); background: transparent; margin: 0; padding: 0; }
.header { padding: 14px 18px 8px; border-bottom: 1px solid var(--border-strong); }
.header h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
.header .meta { color: var(--fg-muted); font-size: 12px; }
.section { border-bottom: 1px solid var(--border-soft); padding: 8px 16px; }
.section summary { cursor: pointer; display: flex; align-items: center; gap: 8px; padding: 6px 2px;
                   list-style: none; font-weight: 600; }
.section summary::-webkit-details-marker { display: none; }
.section summary::before { content: "▸"; font-size: 11px; color: var(--fg-muted); transition: transform .15s; }
.section[open] summary::before { transform: rotate(90deg); }
.sec-title { flex: 1; }
.sec-count { background: var(--chip-bg); color: var(--chip-fg); border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.sec-hint { color: var(--fg-muted); font-size: 12px; padding: 2px 0 8px 16px; }
.repo { margin: 6px 0 12px; }
.repo-name { font-size: 12px; font-weight: 600; color: var(--fg-muted); padding: 6px 8px; background: var(--surface);
             border-left: 3px solid var(--border-strong); border-radius: 3px; margin-bottom: 4px; }
.repo-name .count { font-weight: 400; }
.item { padding: 6px 8px 6px 14px; border-left: 2px solid transparent; margin: 2px 0; }
.item:hover { background: var(--surface-hover); border-left-color: var(--link); }
.row1 { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.title { color: var(--link); text-decoration: none; font-weight: 500; flex: 1; min-width: 0; }
.title:hover { text-decoration: underline; }
.ref { color: var(--fg-muted); font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
.row2 { color: var(--fg-muted); font-size: 12px; margin-top: 2px; padding-left: 30px; }
.row3 { padding-left: 30px; margin-top: 3px; }
.kind { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.kind.pr { background: var(--badge-pr-bg); color: var(--badge-pr-fg); }
.kind.issue { background: var(--badge-issue-bg); color: var(--badge-issue-fg); }
.kind.disc { background: var(--badge-disc-bg); color: var(--badge-disc-fg); }
.lbl { display: inline-block; padding: 1px 6px; margin: 0 4px 2px 0; background: var(--chip-bg); color: var(--chip-fg); border-radius: 10px; font-size: 11px; }
.warn { color: var(--warn); font-weight: 500; }
.reason { color: var(--reason); font-style: italic; }
.empty { color: var(--fg-muted); padding: 4px 14px 8px; font-style: italic; font-size: 13px; }
.empty.err { color: var(--error); }
.actions { padding: 8px 18px; border-bottom: 1px solid var(--border-strong); display: flex; gap: 8px; }
.actions button { font: inherit; padding: 4px 12px; border: 1px solid var(--btn-border); background: var(--btn-bg); color: var(--fg); border-radius: 6px; cursor: pointer; }
.actions button:hover { background: var(--btn-bg-hover); }
.actions button:disabled { opacity: 0.6; cursor: wait; }
#status { color: var(--fg-muted); font-size: 12px; align-self: center; }
.summary { margin-top: 8px; padding: 8px 10px; background: var(--summary-bg); color: var(--summary-fg); border-left: 3px solid var(--summary-border); border-radius: 3px; font-size: 13px; }
.filter-bar { padding: 8px 18px; border-bottom: 1px solid var(--border-strong);
              display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.filter-btn { font: inherit; padding: 3px 10px; border: 1px solid var(--btn-border);
              background: var(--btn-bg); color: var(--fg); border-radius: 12px;
              cursor: pointer; font-size: 12px; line-height: 1.4; }
.filter-btn:hover { background: var(--btn-bg-hover); }
.filter-btn.active { background: var(--link); color: #fff; border-color: var(--link); }
.filter-btn .cnt { margin-left: 5px; font-size: 11px; opacity: 0.75; font-variant-numeric: tabular-nums; }
.filter-btn.active .cnt { opacity: 0.95; }
.filter-empty { color: var(--fg-muted); padding: 4px 14px 8px; font-style: italic; font-size: 13px; }
.dismiss-btn { background: transparent; border: 1px solid transparent; color: var(--fg-muted);
               font-size: 12px; line-height: 1; padding: 2px 7px; border-radius: 4px;
               cursor: pointer; opacity: 0.35; transition: opacity .12s, background .12s, color .12s;
               margin-left: auto; }
.item:hover .dismiss-btn { opacity: 0.95; }
.dismiss-btn:hover { background: var(--btn-bg-hover); border-color: var(--btn-border); color: var(--fg); }
.item.dismissed { opacity: 0.45; }
.item.dismissed:hover { opacity: 1; }
.dismissed-tag { display: none; padding: 1px 6px; border-radius: 10px; font-size: 10px;
                 font-weight: 500; background: var(--chip-bg); color: var(--fg-muted);
                 text-transform: uppercase; letter-spacing: 0.4px; }
.item.dismissed .dismissed-tag { display: inline-block; }
.show-dismissed { display: inline-flex; align-items: center; gap: 5px; color: var(--fg-muted);
                  font-size: 12px; cursor: pointer; user-select: none; }
.show-dismissed input { margin: 0; cursor: pointer; }
"""

    js = """
(function(){
  const THEME_KEY           = 'gh-todo-theme';
  const ORG_FILTER_KEY      = 'gh-todo-org-filter';
  const DISMISS_KEY         = 'gh-todo-dismiss';
  const SHOW_DISMISSED_KEY  = 'gh-todo-show-dismissed';

  const themeBtn   = document.getElementById('themeBtn');
  const refreshBtn = document.getElementById('refreshBtn');
  const showCb     = document.getElementById('showDismissedCb');
  const status     = document.getElementById('status');

  // Theme toggle. Default is dark; user choice persists in localStorage.
  let currentTheme = 'dark';
  try { currentTheme = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
  function applyTheme(t) {
    if (t === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      if (themeBtn) themeBtn.textContent = 'Dark theme';
    } else {
      document.documentElement.removeAttribute('data-theme');
      if (themeBtn) themeBtn.textContent = 'Light theme';
    }
  }
  applyTheme(currentTheme);
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      currentTheme = (currentTheme === 'dark') ? 'light' : 'dark';
      try { localStorage.setItem(THEME_KEY, currentTheme); } catch (e) {}
      applyTheme(currentTheme);
    });
  }

  // ---- Dismissals state -----------------------------------------------
  // A dismissal stores the item's updated_at at the moment of dismissal. On
  // every render we drop entries whose updated_at no longer matches — i.e.
  // any GitHub-side change (label, comment, merge, anything that bumps
  // updated_at) un-dismisses the item. Bot updates count, per spec.
  function loadDismissals() {
    try {
      const raw = localStorage.getItem(DISMISS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) { return {}; }
  }
  function saveDismissals() {
    try { localStorage.setItem(DISMISS_KEY, JSON.stringify(dismissals)); } catch (e) {}
  }
  let dismissals = loadDismissals();

  // Drop stale entries for items present in this render whose updated_at
  // has changed. Entries for items not in this render are left alone — if
  // they come back later we'll re-check then.
  let dismissChanged = false;
  document.querySelectorAll('.item[data-section][data-updated]').forEach(el => {
    const sec = el.getAttribute('data-section');
    const k   = el.getAttribute('data-key');
    const u   = el.getAttribute('data-updated');
    const dk  = sec + ':' + k;
    if (dismissals[dk] && dismissals[dk].updated_at !== u) {
      delete dismissals[dk];
      dismissChanged = true;
    }
  });
  if (dismissChanged) saveDismissals();

  // Sections that are derived views — the agent picks them from items that
  // also appear elsewhere. They don't get their own dismiss button; they
  // inherit dismissed-ness from any "real" section the same item lives in.
  const DERIVED_SECTIONS = new Set(['critical', 'top_picks']);
  const PICK_VISIBLE_LIMIT = 3;  // show top-N non-dismissed candidates

  function isDismissed(el) {
    const sec = el.getAttribute('data-section');
    const k   = el.getAttribute('data-key');
    const u   = el.getAttribute('data-updated');
    if (!sec || !k || !u) return false;
    if (DERIVED_SECTIONS.has(sec)) {
      // Cross-section check: any dismissal of this item (in any "real"
      // section) with matching updated_at counts as dismissed for the pick.
      for (const dk in dismissals) {
        const colon = dk.indexOf(':');
        if (colon < 0) continue;
        const otherSec = dk.slice(0, colon);
        if (DERIVED_SECTIONS.has(otherSec)) continue;     // don't self-reference
        if (dk.slice(colon + 1) !== k) continue;
        if (dismissals[dk].updated_at === u) return true;
      }
      return false;
    }
    const dk = sec + ':' + k;
    return !!(dismissals[dk] && dismissals[dk].updated_at === u);
  }

  // ---- Show-dismissed checkbox ---------------------------------------
  let showDismissed = false;
  try { showDismissed = localStorage.getItem(SHOW_DISMISSED_KEY) === '1'; } catch (e) {}
  if (showCb) showCb.checked = showDismissed;

  // ---- Org filter state ----------------------------------------------
  let orgFilter = '_all';
  try { orgFilter = localStorage.getItem(ORG_FILTER_KEY) || '_all'; } catch (e) {}
  if (orgFilter !== '_all') {
    const sel = '.filter-btn[data-org="' + orgFilter.replace(/"/g, '\\\\"') + '"]';
    if (!document.querySelector(sel)) orgFilter = '_all';
  }

  // ---- Render pass: combine org filter + dismissal visibility, then
  // recompute section badges and per-org button counts from the DOM. The
  // server-side counts shipped in the markup are correct only for "no
  // dismissals", so we always recompute here.
  function applyState() {
    document.body.setAttribute('data-org-filter', orgFilter);

    document.querySelectorAll('.item').forEach(el => {
      const orgMatches = (orgFilter === '_all') || (el.getAttribute('data-org') === orgFilter);
      const dismissed = isDismissed(el);
      el.classList.toggle('dismissed', dismissed);
      const visible = orgMatches && (!dismissed || showDismissed);
      el.style.display = visible ? '' : 'none';
      const btn = el.querySelector('.dismiss-btn');
      if (btn) {
        btn.title = dismissed ? 'Restore (un-dismiss)' : 'Dismiss until any GitHub update';
        btn.textContent = dismissed ? '↶' : '✕';
      }
    });

    // Derived sections (critical, top_picks) get a candidate pool from the
    // agent. After the per-item visibility pass above, cap visible items at
    // PICK_VISIBLE_LIMIT non-dismissed entries (ordered by data-pick-rank).
    // When "Show dismissed" is on, show every candidate without the cap.
    DERIVED_SECTIONS.forEach(secKey => {
      const sec = document.getElementById('sec-' + secKey);
      if (!sec) return;
      const items = Array.from(sec.querySelectorAll('.item'))
        .sort((a, b) => (parseInt(a.getAttribute('data-pick-rank') || '999', 10))
                      - (parseInt(b.getAttribute('data-pick-rank') || '999', 10)));
      let shown = 0;
      items.forEach(el => {
        const orgMatches = (orgFilter === '_all') || (el.getAttribute('data-org') === orgFilter);
        const dismissed = isDismissed(el);
        if (!orgMatches) { el.style.display = 'none'; return; }
        if (showDismissed) { el.style.display = ''; return; }
        if (dismissed) { el.style.display = 'none'; return; }
        if (shown < PICK_VISIBLE_LIMIT) { el.style.display = ''; shown++; }
        else { el.style.display = 'none'; }
      });
    });

    document.querySelectorAll('.repo').forEach(g => {
      const orgMatches = (orgFilter === '_all') || (g.getAttribute('data-org') === orgFilter);
      if (!orgMatches) { g.style.display = 'none'; return; }
      const anyVisible = Array.from(g.querySelectorAll('.item'))
                              .some(it => it.style.display !== 'none');
      g.style.display = anyVisible ? '' : 'none';
    });

    document.querySelectorAll('.section').forEach(sec => {
      const items = sec.querySelectorAll('.item');
      let visibleN = 0;
      items.forEach(el => { if (el.style.display !== 'none') visibleN++; });
      const badge = sec.querySelector('.sec-count');
      if (badge) badge.textContent = visibleN;
      const fe = sec.querySelector('.filter-empty');
      if (fe) fe.style.display = (orgFilter !== '_all' && visibleN === 0 && items.length > 0) ? '' : 'none';
    });

    // Per-org button counts: unique items per org, deduped by data-key
    // across sections, respecting both the dismissal state and the
    // show-dismissed toggle (but ignoring the active org filter — each
    // button shows its own scope).
    document.querySelectorAll('.filter-btn').forEach(b => {
      const org = b.getAttribute('data-org');
      const seen = new Set();
      document.querySelectorAll('.item').forEach(el => {
        const elOrg = el.getAttribute('data-org');
        if (org !== '_all' && elOrg !== org) return;
        if (isDismissed(el) && !showDismissed) return;
        const dk = el.getAttribute('data-key') || '';
        if (dk) seen.add(dk);
      });
      const cnt = b.querySelector('.cnt');
      if (cnt) cnt.textContent = seen.size;
      b.classList.toggle('active', org === orgFilter);
    });

    try {
      localStorage.setItem(ORG_FILTER_KEY, orgFilter);
      localStorage.setItem(SHOW_DISMISSED_KEY, showDismissed ? '1' : '0');
    } catch (e) {}
  }

  document.querySelectorAll('.filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      orgFilter = b.getAttribute('data-org') || '_all';
      applyState();
    });
  });

  document.querySelectorAll('.dismiss-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = btn.closest('.item');
      if (!item) return;
      const sec = item.getAttribute('data-section');
      const k   = item.getAttribute('data-key');
      const u   = item.getAttribute('data-updated');
      if (!sec || !k || !u) return;
      const dk  = sec + ':' + k;
      if (dismissals[dk] && dismissals[dk].updated_at === u) {
        delete dismissals[dk];
      } else {
        dismissals[dk] = { updated_at: u, dismissed_at: new Date().toISOString() };
      }
      saveDismissals();
      applyState();
    });
  });

  if (showCb) {
    showCb.addEventListener('change', () => {
      showDismissed = showCb.checked;
      applyState();
    });
  }

  applyState();

  // Run-task button: trigger the scheduled task. No client-side state needs
  // to be sent — the agent regenerates the candidate pool from scratch and
  // dismissals stay local (kept in localStorage on this device).
  if (!refreshBtn) return;
  refreshBtn.addEventListener('click', async () => {
    refreshBtn.disabled = true;
    status.textContent = 'Triggering task…';
    try {
      if (window.cowork && window.cowork.runScheduledTask) {
        await window.cowork.runScheduledTask('github-radar');
        status.textContent = 'Task triggered — reload this artifact when it finishes.';
      } else {
        status.textContent = 'Run from the scheduled-tasks panel.';
      }
    } catch (e) {
      status.textContent = 'Error triggering task: ' + (e && e.message || e);
    } finally {
      refreshBtn.disabled = false;
    }
  });
})();
"""

    # Tiny inline script that runs before the body is painted, so a user who has
    # chosen light theme doesn't see a flash of the (default) dark theme.
    early_script = """
(function(){
  try {
    var t = localStorage.getItem('gh-todo-theme');
    if (t === 'light') document.documentElement.setAttribute('data-theme', 'light');
  } catch (e) {}
})();
"""

    summary_html = f'<div class="summary">{esc(agent_summary)}</div>' if agent_summary else ""
    js_filled = js

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GitHub Radar — {viewer}</title>
<script>{early_script}</script>
<style>{style}</style>
</head>
<body>
  <div class="header">
    <h1>GitHub Radar — {viewer}</h1>
    <div class="meta">Generated {esc(gen_human)} · orgs: {esc(orgs) or "(none)"} (priority order)</div>
    {summary_html}
  </div>
  <div class="actions">
    <button id="refreshBtn" type="button">Run task now</button>
    <button id="themeBtn" type="button">Light theme</button>
    <label class="show-dismissed"><input id="showDismissedCb" type="checkbox"> Show dismissed</label>
    <span id="status"></span>
  </div>
  {filter_bar_html}
  {body}
<script>{js_filled}</script>
</body>
</html>
"""


def main():
    report = json.load(sys.stdin)
    picks = None
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1]) as f:
                picks = json.load(f)
        except FileNotFoundError:
            print(f"Warning: picks file not found: {e}", file=sys.stderr)
            picks = None
        except json.JSONDecodeError as e:
            print(f"Warning: picks file invalid JSON: {e}", file=sys.stderr)
            picks = None
    sys.stdout.write(render(report, picks))


if __name__ == "__main__":
    main()
