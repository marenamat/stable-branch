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

**Key invariant:** all git mutations (cherry-pick, rebase, delete, amend) run inside a
detached git worktree at `/tmp/stable-branch-<pid>`. The user's working trees are never
touched. The worktree is cleaned up on exit (atexit + SIGTERM/SIGINT handlers).

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

The `author` field in commit data uses `"Name <email>"` format (from `%aN <%aE>` in
`git log`). This matches what `git commit --amend --author` expects.

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
                        [--remote REMOTE] [--no-open] /repo branch [branch ...]
```

- `open_browser` defaults to `True`; pass `--no-open` or set `open_browser = false` in TOML to suppress.
- If any listed branch is missing from the repo, the tool refuses to start and lists the missing branches.

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

## Restart mechanism

The **restart** button in the header calls `POST /api/restart`. The server:
1. Touches `/tmp/stable-branch-restart-{pid}` as a flag file.
2. Sends `SIGTERM` to itself, causing uvicorn to shut down.
3. After `uvicorn.run()` returns in `__main__.py`, the flag file is detected.
4. `os.execv()` replaces the process with a fresh instance (`python -m stable_branch` + original argv + `--no-open`).

The frontend polls `GET /api/state` every 500 ms after the restart and reloads once the new server responds.

## Autosquash

Clicking `⊕` on a `fixup!` or `squash!` commit calls `POST /api/operation` with `type: autosquash`. The backend (`GitWorktree.autosquash`):
1. Strips all `fixup!`/`squash!` prefixes from the title to find the root target title.
2. Locates the original commit in the branch by title match.
3. Runs `git rebase -i --autosquash <parent-of-original>` with `GIT_SEQUENCE_EDITOR=true` and `GIT_EDITOR=true` to suppress all interactive prompts.
4. Rejects the operation if there are merge commits in the rebase range.

## Worktree cleanup after ungraceful kill

If the process was killed and left a stale worktree:
```bash
git worktree list          # find the stale entry
git worktree remove --force /tmp/stable-branch-<pid>
```
