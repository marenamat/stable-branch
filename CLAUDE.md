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
  style.css     16-color group palette, commit card styles
  app.js        WebSocket client, Sortable.js drag-and-drop, overlay/dialog logic
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
port = 8000

[match]
threshold = 0.80   # difflib ratio; lower = more matches
by_author = false  # also require same author to match

[beginnings]
"stable/v1" = "v1.0"   # tag or SHA where this branch starts
"stable/v2" = "v2.0"
```

CLI:
```
python -m stable_branch [--port N] [--config stable-branch.toml] /repo branch [branch ...]
```

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Running

```bash
python -m stable_branch /path/to/repo main stable/v1 stable/v2
# Opens http://localhost:8000
```

## Tests

```bash
pytest tests/test_git_ops.py tests/test_matcher.py -v   # unit (no browser)
pytest tests/e2e/ -v                                      # browser e2e (Playwright)
pytest -v                                                 # everything
```

## Hidden commits

Hidden commits are persisted in `.git/stable-branch-hidden` (JSON list of SHAs).
To flush all hidden commits: `POST /api/hidden/flush` or restart with `--flush-hidden`.

## Worktree cleanup after ungraceful kill

If the process was killed and left a stale worktree:
```bash
git worktree list          # find the stale entry
git worktree remove --force /tmp/stable-branch-<pid>
```
