---
name: daily-github-todo
description: Daily GitHub TODO report
---

You are running the daily GitHub TODO report. Each run is a fresh session with no memory of previous runs — everything you need is in this prompt and the helper scripts sitting next to it.

## Location

When following text mentions `{HOME}`, it refers to `/Users/anty/Documents/Claude/Scheduled/daily-github-todo` path - substitute all `{HOME}` occurences before executing.

## Plan

When you start, call TaskCreate to track your steps, then mark each in_progress / completed as you go:
  0. Run `setup.sh` → produces `$SCRIPTS_DIR/.load-env.sh`
  1. Run the report script → `$WORK_DIR/report.json`
  2. Read `report.json` and choose an ordered pool of 4–6 top-pick candidates (and 0–6 critical candidates) for today
  3. Render → `$WORK_DIR/report.html` and update the artifact

==========================================================
STEP 0 — Setup (run this exactly once, first thing).

```bash
bash "{HOME}/setup.sh"
```

This prints the resolved `SCRIPTS_DIR` and `WORK_DIR` environment variables and generates `.load-env.sh`.

==========================================================
STEP 1 — Generate the report data.

```bash
set -euo pipefail
source "{HOME}/.load-env.sh"
python3 "$SCRIPTS_DIR/gh_report.py" \
  > "$WORK_DIR/report.json" 2>"$WORK_DIR/timings.txt"
echo "--- timings ---"; cat "$WORK_DIR/timings.txt"
echo "--- json size ---"; wc -c "$WORK_DIR/report.json"
```

The script normally completes in 5–30 seconds. If you see a non-zero exit, capture stderr; the failure surface in step 3 is a fallback HTML with the error message.

==========================================================
STEP 2 — Reason about today's priorities (this is the important part).

Read `$WORK_DIR/report.json`. The JSON shape is:

```
{
  "generated_at": "...",
  "viewer": "<user's github login>",
  "orgs": ["<org1>", "<org2>", ...],
  "solo_scopes": ["<viewer>", "<sole-member-org>", ...],
  "windows": { "two_weeks_ago": "...", "seven_days_ago": "...", "sixty_days_ago": "..." },
  "sections": {
    "assigned": [{repo, items: [...]}],   // includes everything in `solo_scopes`, even without a GH assignee
    "authored": [...],
    "review_requested": [...],
    "threads_waiting": [...],          // each item has `threads_waiting: <int>`
    "mentions_unanswered": [...],      // each item has `threads_waiting: <int>`
    "new_unattended": [...],           // solo_scopes skipped — nothing is "unassigned" there
    "new_discussions": [...],
    "stale_closed": [...],             // each item has `closed_by: "<bot login>"`
    "recent_merges_uninvolved": [...]  // solo_scopes skipped — nobody else to be involved
  }
}
```

`solo_scopes` = the viewer's personal namespace + any orgs where they're the sole member. The user doesn't use the assignees field in these scopes (no one else to assign to), so the script treats everything open there as implicitly the user's. Keep that in mind when reasoning about priorities — a Anty0/* item with no assignee is still very much "on the user's plate".

Each item dict has: repo, number, title, url, kind ("issue"/"pr"/"discussion"), state, author, assignees, labels, comments, draft, created_at, updated_at. The user wants the daily picks to be **your judgement**, not an algorithm. Read through the report and ask yourself:

- **Critical**: is anything urgent or dangerous? Look at labels ("critical", "urgent", "security", "P0", "P1", "regression", "outage"), titles mentioning production / data loss / leaks / auth bypasses, fresh user bug reports with serious symptoms, etc. There is often nothing critical — send `"critical": []` rather than inventing things.
- **Top picks**: what would you genuinely recommend the user tackle today? Consider:
    * Reviews that have been waiting many days (especially blocking other people)
    * Threads waiting on the user's response (they're blocking someone else)
    * Mentions that haven't been replied to
    * The user's own PRs sitting open without progress
    * New unattended issues/discussions where the user is the natural responder
    * Items in priority-order orgs (the JSON's `orgs` list — already alphabetised, but you should weigh things in user's primary org/repo activity heavier; infer that from where the user is most active in this report)
    * Prefer variety over piling all picks from a single section. But don't sacrifice quality for variety — if one pick is clearly more important than three weaker ones from different sources, rank it highest and let the weaker ones trail.
    * Skip noise: items that are sitting because they're genuinely blocked on someone else, draft PRs the user just opened, etc.
- **Agent summary (optional)**: one short sentence framing the day at the top of the artifact. Use it to call out a pattern ("two stale reviews on the mobile SDK are the biggest blocker today"). Skip it if nothing notable.

**Output a candidate POOL, not a final shortlist.** Top picks and critical sections are rendered as ordered candidate pools — the artifact's JS shows the top three non-dismissed candidates at a time. When the user dismisses one (in its underlying section), the next candidate in the pool slides up automatically. So your job is:

- Produce **4–6 top-pick candidates** ordered by confidence (most-recommended first). If you genuinely have only 2 good candidates, send 2 — better fewer than padded. If you have a clear single winner, still produce 2–3 alternates so dismissal has somewhere to fall back to.
- Produce **0–6 critical candidates**. Most days this is `[]`. When something is real (P0 label, fresh prod incident, security issue), list it. If multiple things are genuinely critical, order them.
- Write a brief, distinct `reason` for each candidate — each one needs to justify its own slot, not lean on the others.

Dismissals: ignore them when picking. The user dismisses items in their native sections (assigned, review_requested, etc.) and the artifact handles "skip the dismissed pick → promote the next candidate" client-side. You don't need to track or read dismissal state.

Validate your picks/critical items by listing comments, review notes, and similar context using `gh` (already on PATH and authenticated via `GH_TOKEN` after sourcing `.load-env.sh`). Useful one-liners:

```bash
# Issue / PR metadata + last few comments
gh issue view 3201 --repo tolgee/tolgee-platform --comments
gh pr view 3201 --repo tolgee/tolgee-platform --comments

# PR review state (approved / changes-requested / pending)
gh api repos/tolgee/tolgee-platform/pulls/3201/reviews \
  --jq '.[] | {user: .user.login, state, submitted_at}'

# Discussions (GraphQL only — gh has no first-class discussion subcommand)
gh api graphql -f query='
  query($owner:String!,$name:String!,$number:Int!){
    repository(owner:$owner,name:$name){
      discussion(number:$number){ title updatedAt
        comments(last:5){ nodes { author{login} createdAt body } } } } }
' -F owner=tolgee -F name=tolgee-platform -F number=3685
```

If reading comments makes you realize an item is less important than it looked, check a few more candidates before settling.

Write your decision to `$WORK_DIR/picks.json` as ordered candidate pools (highest-confidence first):

```json
{
  "agent_summary": "optional one-liner or omit",
  "critical": [
    {"repo": "owner/name", "number": 123, "reason": "why this is critical"}
  ],
  "top_picks": [
    {"repo": "owner/name", "number": 456, "reason": "why this is a good pick today"},
    {"repo": "owner/name", "number": 789, "reason": "alternate if 456 gets dismissed"}
  ]
}
```

If there's truly nothing critical, send `"critical": []`. If you can't pick anything meaningful for top_picks, send `"top_picks": []`. Otherwise aim for 4–6 ranked top_pick candidates.

==========================================================
STEP 3 — Render and update the artifact.

```bash
set -euo pipefail
source "{HOME}/.load-env.sh"
python3 "$SCRIPTS_DIR/render_report.py" \
  "$WORK_DIR/picks.json" \
  < "$WORK_DIR/report.json" \
  > "$WORK_DIR/report.html"
wc -c "$WORK_DIR/report.html"
echo "report at: $WORK_DIR/report.html"
```

Quick sanity check: Read the first ~30 lines of `$WORK_DIR/report.html` to confirm it's well-formed HTML and contains a `<title>GitHub TODO`. If `wc -c` returned < 1000 bytes, write a minimal fallback HTML into `$WORK_DIR/report.html` noting the error from `$WORK_DIR/timings.txt` and use that instead.

Then call `mcp__cowork__update_artifact` with:
  id: "daily-github-todo"
  update_summary: "Daily GitHub TODO refresh — <today's date YYYY-MM-DD>"
  html_path: the absolute path printed by the bash block above (i.e. `$WORK_DIR/report.html`). Echo it in a bash call if you need the literal to paste.
  mcp_tools: `[]` — the page uses `sendPrompt(...)` (no MCP write tools needed). Passing the empty list explicitly resets any prior allowlist; if you omit `mcp_tools` the previous list is retained.

==========================================================
Finish with a one-line confirmation in chat:

> Updated the daily-github-todo artifact. Top picks for today:
> - <repo#num> — <title> (<reason>)
> - …

Pull those lines from `$WORK_DIR/picks.json`. If `critical` was non-empty, mention how many critical items you flagged.

If — and only if — you hit any issues during the run (script errors, unexpected gh failures, missing data, scope problems, fallbacks you had to take, anything you had to work around), append a short `**Issues encountered:**` section below the confirmation. Keep it to a few bullets, one line each, naming the step and what happened. Omit the section entirely when the run was butter smooth.

==========================================================
STEP 4 — Check for repo updates.

After the confirmation message, run a quick update check on the repo itself so the user knows if there are upstream changes waiting:

```bash
cd "{HOME}" && git fetch --all 2>&1 && git status
```

Interpret the result and tell the user about it as a single short line appended after the confirmation (and after the optional `**Issues encountered:**` section if present):

- If `git fetch` reports "does not appear to be a git repository", "No configured remotes", or prints nothing because no remotes exist → say: `> Repo update check: no remote configured, skipping.`
- If `git status` shows `Your branch is up to date with ...` → say: `> Repo update check: up to date.`
- If `git status` shows `Your branch is behind ... by N commits` → say: `> Repo update check: **N new commit(s) available** — I can update it for you, just ask.`
- If `git status` shows `Your branch and ... have diverged` or local commits ahead → say: `> Repo update check: local branch has diverged from upstream — let me know if I can help with merging the changes.`
- If anything else goes wrong (auth failure, network error, unexpected output) → say: `> Repo update check failed: <one-line summary>.` and add it to `**Issues encountered:**`.

Keep this to one line. Don't repeat the raw `git status` output in chat.
