# GitHub Radar

> NOTE: Ask Claude Cowork to setup this scheduled task for you - all you need to do is add `*.github.com` to allowed domains, Claude can follow the steps bellow on it's own. It'll ask you for everything it needs to setup the task.

A Claude Cowork based scheduled task that produces a daily GitHub Radar report — a view of issues, PRs, reviews, mentions, and threads waiting on you across every org you belong to. The output is rendered into a self-contained HTML artifact (`github-radar`).

## Prerequisites

- macOS with Claude Cowork installed and the Scheduled Tasks feature enabled.
- A GitHub Personal Access Token (classic or fine-grained) with read access to the orgs and repos you want surfaced. Pick whichever style you prefer:

  **Classic PAT** — tick these scopes:
  - `repo` (full control of private repos; needed for issue/PR reads in private repos)
  - `read:org` (list orgs you belong to)
  - `read:discussion` (read repo discussions)

  **Fine-grained PAT** — set "Resource owner" to org/user for which you want to see private repositories. All other orgs/user will only list public repositories. Under "Repository permissions" set:
  - `Contents` → Read
  - `Issues` → Read
  - `Pull requests` → Read
  - `Discussions` → Read
  - `Metadata` → Read (auto-selected, required)

  And under "Organization permissions":
  - `Members` → Read (so the script can enumerate orgs and detect solo-member scopes)

## Install the skill in Claude Cowork

1. **Create an empty scheduled task in Cowork.**
   Open Claude Cowork → Scheduled Tasks → *New scheduled task*. Name it `github-radar` (the name becomes the directory name, and the artifact id in the rendered HTML expects this slug — if you pick a different name you'll need to update `id: "github-radar"` in `SKILL.md`'s STEP 3 and `runScheduledTask('github-radar')` in `render_report.py`). Leave the prompt body empty and save. Pick whatever schedule you want (e.g. every morning at 08:00).

2. **Locate the directory Cowork just created.**
   Cowork will have generated:

   ```
   ~/Documents/Claude/Scheduled/github-radar/
   └── SKILL.md          # auto-generated stub from your empty prompt
   ```

3. **Replace that directory with this repository.**
   Delete the auto-generated directory and clone this repo in its place — the folder name must stay `github-radar` (or whatever name you chose in step 1):

   ```bash
   cd ~/Documents/Claude/Scheduled
   rm -rf github-radar
   git clone <path-or-url-to-this-repo> github-radar
   ```

4. **Drop in your GitHub token.**
   Create `.token` at the repo root and paste your PAT into it (single line, no surrounding whitespace — `setup.sh` strips whitespace but won't tolerate an empty file):

   ```bash
   cd ~/Documents/Claude/Scheduled/github-radar
   printf '%s' 'ghp_yourTokenHere' > .token
   chmod 600 .token
   ```

5. **Do a dry run.**
   Trigger the scheduled task once from Cowork (or wait for the next scheduled fire). On the first run, `setup.sh` downloads the `gh` CLI into `bin/` (≈10 MB). Agent will create a live artifact with the report - make sure to hit allow when prompted.

## Use it without Claude (CLI-only)

You don't need Cowork to use this repo — `run.sh` does everything `SKILL.md` does except the agent reasoning step. You get the same HTML report, just without the "Critical" and "Top picks" sections at the top (those are picked by the agent each day).

Requirements: a POSIX shell, `bash`, `python3`, `git`, and network access. The repo bootstraps its own `gh` binary on first run, so you don't need `gh` preinstalled.

1. **Clone the repo and add your token** (same as steps 3–4 above, anywhere on disk — it doesn't have to live under `~/Documents/Claude/Scheduled/`):

   ```bash
   git clone <path-or-url-to-this-repo> github-radar
   cd github-radar
   printf '%s' 'ghp_yourTokenHere' > .token
   chmod 600 .token
   ```

2. **Run the report**:

   ```bash
   bash run.sh           # writes outputs/<today>_NN/report.html and prints the path
   bash run.sh --open    # same, plus opens the HTML in your default browser
   ```

   Each invocation creates a fresh `outputs/<today>_NN/` directory (NN auto-increments), so previous runs are not overwritten.

3. **(Optional) Schedule it yourself.** Drop something like this into your crontab to get a fresh report every weekday at 08:00:

   ```
   0 8 * * 1-5  cd /path/to/github-radar && bash run.sh >/dev/null 2>&1
   ```

   Then point your browser at `outputs/$(date -u +%Y-%m-%d)_01/report.html` when you want to read it (or wire it into whatever viewer you prefer).
