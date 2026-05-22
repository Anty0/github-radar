---
name: daily-github-todo
description: Daily GitHub TODO report
---

You are running the daily GitHub TODO report. Each run is a fresh session with no memory of previous runs — everything you need is in this prompt and the two helper scripts sitting next to it.

When you start, call TaskCreate to track your steps, then mark each in_progress / completed as you go:
  1. Run the report script → report.json
  2. Read report.json and choose 1–3 top picks (and any critical flags) for today
  3. Render → report.html and update the artifact

The two helper scripts live in this task's own folder:
  - /Users/anty/Documents/Claude/Scheduled/daily-github-todo/gh_report.py
  - /Users/anty/Documents/Claude/Scheduled/daily-github-todo/render_report.py

GitHub credentials (the user's classic PAT, scoped to repo/read:org/discussions). Use it ONLY as the GH_TOKEN env var — never echo it to chat output:
  GH_TOKEN=ghp_REDACTED

==========================================================
STEP 1 — Generate the report data.

Run this bash command exactly. It writes the categorised report data to $PWD/gh-todo/report.json. The script discovers the user's GitHub login and current org memberships at runtime, so you don't pass them in.

```bash
set -euo pipefail
WORK="$PWD/gh-todo"
mkdir -p "$WORK"
export GH_TOKEN='ghp_REDACTED'
python3 /Users/anty/Documents/Claude/Scheduled/daily-github-todo/gh_report.py \
  > "$WORK/report.json" 2>"$WORK/timings.txt"
echo "--- timings ---"; cat "$WORK/timings.txt"
echo "--- json size ---"; wc -c "$WORK/report.json"
```

The script normally completes in 5–30 seconds. If you see a non-zero exit, capture stderr; the failure surface in step 3 is a fallback HTML with the error message.

==========================================================
STEP 2 — Reason about today's priorities (this is the important part).

Read `$WORK/report.json`. The JSON shape is:

```
{
  "generated_at": "...",
  "viewer": "<user's github login>",
  "orgs": ["<org1>", "<org2>", ...],
  "windows": { "two_weeks_ago": "...", "seven_days_ago": "...", "sixty_days_ago": "..." },
  "sections": {
    "assigned": [{repo, items: [...]}],
    "authored": [...],
    "review_requested": [...],
    "threads_waiting": [...],          // each item has `threads_waiting: <int>`
    "mentions_unanswered": [...],      // each item has `threads_waiting: <int>`
    "new_unattended": [...],
    "new_discussions": [...],
    "stale_closed": [...],             // each item has `closed_by: "<bot login>"`
    "recent_merges_uninvolved": [...]
  }
}
```

Each item dict has: repo, number, title, url, kind ("issue"/"pr"/"discussion"), state, author, assignees, labels, comments, draft, created_at, updated_at. The user wants the daily picks to be **your judgement**, not an algorithm. Read through the report and ask yourself:

- **Critical**: is anything urgent or dangerous? Look at labels ("critical", "urgent", "security", "P0", "P1", "regression", "outage"), titles mentioning production / data loss / leaks / auth bypasses, fresh user bug reports with serious symptoms, etc. There is often nothing critical — say so rather than inventing it.
- **Top picks (1–3)**: what would you genuinely recommend the user tackle today? Consider:
    * Reviews that have been waiting many days (especially blocking other people)
    * Threads waiting on the user's response (they're blocking someone else)
    * Mentions that haven't been replied to
    * The user's own PRs sitting open without progress
    * New unattended issues/discussions where the user is the natural responder
    * Items in priority-order orgs (the JSON's `orgs` list — already alphabetised, but you should weigh things in user's primary org/repo activity heavier; infer that from where the user is most active in this report)
    * Prefer variety over piling all picks from a single section. But don't sacrifice quality for variety — if one pick is clearly more important than three weaker ones from different sources, recommend just that one.
    * Skip noise: items that are sitting because they're genuinely blocked on someone else, draft PRs the user just opened, etc.
- **Agent summary (optional)**: one short sentence framing the day at the top of the artifact. Use it to call out a pattern ("two stale reviews on the mobile SDK are the biggest blocker today"). Skip it if nothing notable.

Write your decision to `$WORK/picks.json` as:

```json
{
  "agent_summary": "optional one-liner or omit",
  "critical": [
    {"repo": "owner/name", "number": 123, "reason": "why this is critical"}
  ],
  "top_picks": [
    {"repo": "owner/name", "number": 456, "reason": "why this is a good pick today"}
  ]
}
```

If there's truly nothing critical, send `"critical": []`. If you can't pick anything meaningful, send `"top_picks": []` — better empty than padded.

==========================================================
STEP 3 — Render and update the artifact.

```bash
WORK="$PWD/gh-todo"
python3 /Users/anty/Documents/Claude/Scheduled/daily-github-todo/render_report.py \
  "$WORK/picks.json" \
  < "$WORK/report.json" \
  > "$WORK/report.html"
wc -c "$WORK/report.html"
cp "$WORK/report.html" "$PWD/report.html"
echo "saved at: $PWD/report.html"
```

Quick sanity check: Read the first ~30 lines of report.html to confirm it's well-formed HTML and contains a `<title>GitHub TODO`. If `wc -c` returned < 1000 bytes, write a minimal fallback HTML noting the error from `$WORK/timings.txt` and use that instead.

Then call `mcp__cowork__update_artifact` with:
  id: "daily-github-todo"
  update_summary: "Daily GitHub TODO refresh — <today's date YYYY-MM-DD>"
  html_path: <absolute host path to report.html in your outputs folder — take the WORKSPACE FOLDER path from your system prompt and append "/report.html">

Do NOT include `mcp_tools`.

==========================================================
Finish with a one-line confirmation in chat:

> Updated the daily-github-todo artifact. Top picks for today:
> - <repo#num> — <title> (<reason>)
> - …

Pull those lines from "$WORK/picks.json". If `critical` was non-empty, mention how many critical items you flagged.