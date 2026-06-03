# Stable Branch

The stable-branch project is a simple python/browser tool displaying the git
history of multiple branches at once. The order of these branches is
configurable, as well as their beginnings.

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

## Ordering / Cherry-picking

The tool allows to drag-and-drop commits to reorder them in the branches.

The tool allows to drag-and-drop commits between branches (by cherry-pick and/or rebase).

The tool allows to delete commits in branches (by a button). Deleted commits
are stored in an additional view, from where they can be dragged and dropped
back into the tree.

Whenever any operation fails (e.g. on merge conflict), the full output is
displayed to the user and everything rolled back. Also the command is
displayed, so that the user may run it manually and resolve the problem.
