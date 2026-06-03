# stable-branch

Browser UI for viewing and managing git history across multiple branches
simultaneously — built for the stable branch workflow where commits are
backported across release branches such as `main`, `stable/v1`, `stable/v2`.

Commits that are the same logical change on different branches are
automatically detected and shown in the same color. You can cherry-pick,
reorder, and delete commits by dragging.

See [DESIGN.md](DESIGN.md) for the feature specification.

## Install

Requires Python 3.12+.

```bash
git clone https://github.com/yourname/stable-branch
cd stable-branch
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Run

```bash
python -m stable_branch /path/to/repo main stable/v1 stable/v2
```

Opens at `http://localhost:8000`. Pass `--open` to open the browser automatically.

## Config

CLI flags can also be set in `stable-branch.toml` in the current directory
(CLI overrides TOML):

```toml
repo     = "/path/to/repo"
branches = ["main", "stable/v1", "stable/v2"]
port     = 8000

[match]
threshold = 0.80    # commit title similarity required to group commits (0–1)
by_author = false   # also require the same author

[beginnings]
"stable/v1" = "v1.0"   # tag or SHA where each branch starts
"stable/v2" = "v2.0"
```

All CLI options:

| Flag | Default | Description |
|---|---|---|
| `--port N` | `8000` | Port to listen on |
| `--config FILE` | `stable-branch.toml` | TOML config file |
| `--match-threshold F` | `0.80` | Similarity threshold for grouping commits |
| `--match-by-author` | off | Also require same author to match |
| `--beginning BRANCH=REF` | — | Tag or SHA where a branch starts (repeatable) |
| `--flush-hidden` | off | Clear all hidden commits on startup |
| `--open` | off | Open browser tab automatically |

## UI

Each branch is a column. Commits run newest-to-oldest from top to bottom.
Commits that appear on more than one branch are shown in the same color.

**Viewing:**
- Click a colored commit title to see the diff-of-diffs between its matching commits across branches.

**Hiding:**
- Click `−` on a commit to hide it. Hidden commits collapse into a thin bar.
- Click the bar to see and restore hidden commits.
- Click **flush hidden** in the header to show all hidden commits at once.
- Hidden commits are remembered across restarts (stored in `.git/stable-branch-hidden`).

**Moving:**
- Drag a commit within a column to reorder it (interactive rebase).
- Drag a commit to a different column to cherry-pick it onto that branch.
- Drag a commit to the **deleted** panel on the right to remove it from the branch.
- Drag a commit from the **deleted** panel back into a column to restore it.

**Errors:**
- If an operation fails (e.g. merge conflict), the exact git command and its
  full output are shown in a dialog. Nothing is left in a broken state.

## Worktree cleanup after ungraceful kill

All git mutations run in a detached worktree at `/tmp/stable-branch-<pid>`,
which is removed on clean exit. If the process is killed, remove it manually:

```bash
git worktree list
git worktree remove --force /tmp/stable-branch-<pid>
```

## Development

```bash
pip install -e ".[dev]"
playwright install chromium

pytest tests/test_git_ops.py tests/test_matcher.py -v   # unit tests
pytest tests/e2e/ -v                                      # browser e2e tests
```
