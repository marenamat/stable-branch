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

Prints the URL (random free port by default). Pass `--open` to open the browser automatically.

## Config

CLI flags can also be set in `stable-branch.toml` in the current directory
(CLI overrides TOML):

```toml
repo     = "/path/to/repo"
branches = ["main", "stable/v1", "stable/v2"]
port     = 8000

hide_merges = true   # auto-hide merge commits
issue_url   = "https://github.com/org/repo/issues/"   # prefix for #N badges

relevant_remotes = ["origin", "upstream"]   # show remote-tracking ref badges

[match]
threshold = 0.80    # commit title similarity required to group commits (0–1)
by_author = false   # also require the same author

[beginnings]
"stable/v1" = "v1.0"   # tag or SHA where each branch starts
"stable/v2" = "v2.0"

[filter.hide_if]
# auto-hide commits whose body contains a matching mail-style header
Character = ["experimental", "wip"]

[filter.highlight_if]
# highlight commits (colored right border) by matching mail-style header
Priority = ["high", "critical"]
```

All CLI options:

| Flag | Default | Description |
|---|---|---|
| `--port N` | random | Port to listen on |
| `--config FILE` | `stable-branch.toml` | TOML config file |
| `--match-threshold F` | `0.80` | Similarity threshold for grouping commits |
| `--match-by-author` | off | Also require same author to match |
| `--beginning BRANCH=REF` | — | Tag or SHA where a branch starts (repeatable) |
| `--flush-hidden` | off | Clear all hidden commits on startup |
| `--open` | off | Open browser tab automatically |
| `--hide-merges` | off | Auto-hide merge commits (show as strips) |
| `--issue-url URL` | — | URL prefix for `#N` issue/PR link badges |
| `--remote REMOTE` | — | Show remote-tracking ref badges for this remote (repeatable) |

## UI

Each branch is a column. Commits run newest-to-oldest from top to bottom.
Commits that appear on more than one branch are shown in the same color.

**Viewing:**
- Click a colored commit title to see the diff and, for grouped commits, a range-diff between matching commits across branches.
- Tags, local branch heads, and remote-tracking refs pointing to visible commits are shown as small badges on the commit card.

**Hiding:**
- Click `−` on a commit to hide it. Hidden commits collapse into a thin bar.
- Click the bar to see and restore hidden commits.
- Click **flush hidden** in the header to show all hidden commits at once.
- Hidden commits are remembered across restarts (stored in `.git/stable-branch-hidden`).
- With `hide_merges = true`, merge commits are automatically hidden on startup.
- With `[filter.hide_if]` rules, commits matching a mail-style header are automatically hidden. Auto-hidden commits can be individually unhidden via the strip dialog, and stay visible on the next reload.

**Highlighting:**
- With `[filter.highlight_if]` rules, commits matching a header are highlighted with a colored right border and tinted background. Up to 8 distinct colors are used (first matching rule wins).

**Issue / PR links:**
- If `issue_url` is configured, any `#N` pattern in a commit message is rendered as a purple badge linking to `issue_url + N`. Clicking opens the URL in a new tab.

**Branch beginnings:**
- If a branch has a configured beginning (a tag or SHA), commits from other branches that predate that point are shown as dimmed, dashed ghost cards. This makes it clear which commits already existed before the branch diverged.

**Moving:**
- Drag a commit within a column to reorder it (interactive rebase).
- Drag a commit to a different column to cherry-pick it onto that branch.
- Use the `↑` / `↓` buttons to reorder commits one step at a time.

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

pytest tests/test_git_ops.py tests/test_matcher.py tests/test_server.py -v   # unit tests
pytest tests/e2e/ -v                                                           # browser e2e tests (needs Chromium)
pytest -v                                                                      # everything
```

E2e tests use Selenium with Chromium. On Ubuntu/Debian: `apt install chromium-browser`. On Alpine: `apk add chromium`.
