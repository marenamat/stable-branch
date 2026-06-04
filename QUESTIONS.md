# Open Questions

Questions that came up during design. Decisions made during planning are marked **Decided**.

## Commit matching

- **Decided:** Matching threshold (default 0.80) and whether to also match by author are both
  user-configurable settings (`match.threshold`, `match.by_author` in TOML / `--match-threshold`
  / `--match-by-author` on CLI).

## File watching

- **Decided:** watchdog (inotify on Linux) is sufficient. No need to expose fanotify.

## Branch beginnings

- **Decided:** A branch beginning is specified as a tag name or commit SHA in the `[beginnings]`
  section of `stable-branch.toml` (or `--beginning branch=ref` on CLI).

## Config format

- **Decided:** Both CLI args and `stable-branch.toml` are supported. CLI overrides TOML.

## Conflict UX

- **Decided:** On failure, show the raw git output (`stdout + stderr`) and the exact git command
  that failed, in a `<pre>` modal. No inline diff needed.

## Browser support

- **Decided:** Firefox and Chrome are required. Other browsers are nice-to-have.

## Security

- **Decided:** No authentication. The server binds to `localhost` only (`127.0.0.1`).

## Worktree cleanup on ungraceful kill

- **Decided:** Document manual cleanup (see CLAUDE.md). No `--clean` flag needed.

## Hidden commit persistence

- **Decided:** Hidden commits persist across restarts in `.git/stable-branch-hidden`.
  Users can flush them via `POST /api/hidden/flush` (or a UI button) or by passing
  `--flush-hidden` at startup.

## Open / undecided

- What should happen when a branch listed in config no longer exists in the repo?
  (Currently: skip it with a warning in stderr.)
  -> refuse to start and write out all branches which are gone

- Auto-open browser on startup is opt-in via `--open`; default is to
  print the URL only.
  -> actually auto-open the browser tab

- What is the right debounce window for the inotify watcher before recomputing?
  (Currently: 200 ms after the last event.)
  -> this probably good

- Should reorder operations be restricted to contiguous ranges, or allow arbitrary
  reordering of non-adjacent commits? (Currently: arbitrary, relying on git rebase -i.)
  -> allow arbitrary reordering but never across merges
