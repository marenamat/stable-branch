# Design

stable-branch is a Python/browser tool displaying the git history of multiple
branches at once. The order of these branches is configurable, as well as their
beginnings.

The tool creates an additional git worktree in /tmp (or more), with a detached
head, to do all the operations needed without bothering the existing worktrees.

## Basic

Branches are displayed vertically, top newest commits, bottom oldest commits, colored.

These branches contain similar or identical commits. The tool matches these
commits (by title, author, date) and displays them with the same color.

The display is auto-updated whenever git changes, via fanotify or inotify.

The tool also displays tags and other branch heads pointing into the displayed trees.

## Inspection

The tool allows to display a diff of the diffs for each group of similar commits.

The tool allows to hide commits (by a button), and these will collapse into a marker which would display them in an overlay, and allow possibly unhiding them (by a button).

## Editing

The tool allows editing a commit's message and author via a `✎` button on each card.
When a commit belongs to a matched group (the same logical change present on N branches),
the edit dialog presents all N branches and propagates the change to all of them with a
single Save. This covers the common workflow of fixing a commit message (e.g. adding a
fixes/closes reference) that was backported to several branches.

Editing is implemented as `git rebase -i` with an `exec git commit --amend` step for the
target commit. It fails if there are merge commits above the target commit on the branch
(rebasing across merges would corrupt the merge's parent chain).

## Ordering / Cherry-picking

The tool allows to drag-and-drop commits to reorder them in the branches.

The tool allows to drag-and-drop commits between branches (by cherry-pick and/or rebase).

The tool allows to delete commits in branches (by a button). Deleted commits
are stored in an additional view, from where they can be dragged and dropped
back into the tree.

Merge commits are anchors: they cannot be dragged or reordered, and other
commits cannot be moved across a merge boundary. Reordering is only supported
within the top segment (commits above the first merge in the branch).

Whenever any operation fails (e.g. on merge conflict), the full output is
displayed to the user and everything rolled back. Also the command is
displayed, so that the user may run it manually and resolve the problem.

## Startup validation

If any branch listed in the configuration does not exist in the repository,
the tool refuses to start and prints all missing branch names to stderr.

The browser opens automatically on startup. Pass `--no-open` to suppress this.

## Filtering and auto-hiding

Commits can be automatically hidden on load based on two criteria:

- **Merge commits** — if `hide_merges = true`, any commit with more than one parent
  is hidden. Configured via TOML top-level key or `--hide-merges` CLI flag.

- **Commit body headers** — commits can carry mail-style headers in their body
  (e.g. `Character: experimental`). The `[filter.hide_if]` TOML section maps
  header names to lists of values; a commit matching any entry is auto-hidden.

Auto-hidden commits appear as thin strips (same as manually hidden commits), not
silently removed. Users can click a strip to see and individually unhide commits.
Once unhidden, a commit stays visible across restarts even if the auto-hide rule
still matches — the SHA is added to `.git/stable-branch-shown` as a force-shown
override.

## Highlighting

The `[filter.highlight_if]` TOML section maps header names to lists of values.
Commits whose body contains a matching header are highlighted with a colored right
border and a tinted background. Up to 8 distinct colors are used; the first
matching rule wins. Highlighting is purely visual — it does not affect hide/show
state.

## Issue / PR link badges

If `issue_url` is configured (e.g. `"https://github.com/org/repo/issues/"`), the
tool scans the full commit message for `#N` patterns and renders each as a small
purple badge on the commit card. Clicking a badge opens `issue_url + N` in a new
tab. Duplicate references in a single commit message are shown only once.

## Remote-tracking ref badges

Tags and local branch heads pointing to visible commits are shown as small badges
on commit cards. The `relevant_remotes` config option (TOML list or repeatable
`--remote REMOTE` CLI flag) enables the same for remote-tracking refs (e.g.
`origin/main`). Remote badges are shown in blue, distinct from local branch
(green) and tag (yellow) badges.

## Branch beginnings and pre-beginning ghosts

A branch beginning is a tag or SHA marking where a stable branch was cut from
its parent. Commits on other branches that predate a beginning are shown as
dimmed, dashed "ghost" cards in that branch's column — indicating that those
commits already exist in the branch's history before the cutoff point. Ghost
cards are not draggable and are not subject to hide/show rules.
