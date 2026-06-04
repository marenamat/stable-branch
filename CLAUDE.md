# stable-branch

A Python tool that serves a local browser UI for viewing and manipulating git history across
multiple branches simultaneously — designed for the "stable branch" workflow where commits are
backported across release branches (e.g. `main`, `stable/v1`, `stable/v2`).

## Architecture

```
src/stable_branch/
  __main__.py   CLI entry point; parses args + TOML config, starts uvicorn
  models.py     Dataclasses: Commit, Branch, Group, Config
  git_ops.py    GitWorktree class: creates /tmp worktree, runs cherry-pick/rebase/delete
  matcher.py    Commit similarity matching across branches (difflib + greedy bipartite)
  watcher.py    watchdog observer → asyncio queue → WebSocket broadcast
  server.py     FastAPI app: REST API + WebSocket + serves frontend/

frontend/
  index.html    Multi-column branch layout
  style.css     16-color group palette, commit card styles, highlight palette
  app.js        WebSocket client, drag-and-drop, overlay/dialog logic
```

**Key invariant:** all git mutations (cherry-pick, rebase, delete) run inside a detached
git worktree at `/tmp/stable-branch-<pid>`. The user's working trees are never touched.
The worktree is cleaned up on exit (atexit + SIGTERM/SIGINT handlers).

## Config

Config can come from CLI args or `stable-branch.toml` (CLI overrides TOML).

```toml
# stable-branch.toml
repo = "/path/to/repo"
branches = ["main", "stable/v1", "stable/v2"]
port = 8000                  # omit for random free port

hide_merges = true           # auto-hide merge commits
issue_url   = "https://github.com/org/repo/issues/"
relevant_remotes = ["origin", "upstream"]

[match]
threshold = 0.80   # difflib ratio; lower = more matches
by_author = false  # also require same author to match

[beginnings]
"stable/v1" = "v1.0"   # tag or SHA where this branch starts
"stable/v2" = "v2.0"

[filter.hide_if]
Character = ["experimental", "wip"]   # mail-style header matching

[filter.highlight_if]
Priority = ["high", "critical"]       # highlighted with colored right border
```

All `Config` dataclass fields (in `models.py`):
- `repo_path`, `branches`, `port`
- `match_threshold`, `match_by_author`
- `branch_beginnings: dict[str, str]`
- `flush_hidden: bool`, `open_browser: bool`
- `hide_merges: bool` — auto-hide merge commits
- `hide_if: dict[str, list[str]]` — mail-header auto-hide rules
- `highlight_if: dict[str, list[str]]` — mail-header highlight rules
- `issue_url: str | None` — URL prefix for `#N` badges
- `relevant_remotes: list[str]` — remotes whose tracking refs are shown as badges

CLI:
```
python -m stable_branch [--port N] [--config FILE] [--hide-merges] [--issue-url URL]
                        [--remote REMOTE] ... /repo branch [branch ...]
```

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# e2e tests use Selenium + Chromium (no extra install step needed if chromium is in PATH)
```

## Running

```bash
python -m stable_branch /path/to/repo main stable/v1 stable/v2
# Prints URL (random port by default)
```

## Tests

```bash
pytest tests/test_git_ops.py tests/test_matcher.py tests/test_server.py -v   # unit (no browser)
pytest tests/e2e/ -v                                                           # browser e2e (Selenium)
pytest -v                                                                      # everything
```

## Hidden commits

Two persistence files live under `.git/` (or the git common dir for worktrees):

- `.git/stable-branch-hidden` — JSON list of manually hidden SHAs
- `.git/stable-branch-shown` — JSON list of force-shown SHAs (override auto-hide rules)

When a user unhides an auto-hidden commit, its SHA is added to `stable-branch-shown` so it
stays visible across reloads even if the auto-hide rule still matches.

To flush all hidden commits: `POST /api/hidden/flush` or restart with `--flush-hidden`.

## Worktree cleanup after ungraceful kill

If the process was killed and left a stale worktree:
```bash
git worktree list          # find the stale entry
git worktree remove --force /tmp/stable-branch-<pid>
```
