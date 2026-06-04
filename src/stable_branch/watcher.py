import asyncio
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

_WRITE_EVENT_TYPES = (FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent)


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, git_dir: Path):
        self._queue = queue
        self._loop = loop
        self._git_dir = git_dir

    def on_any_event(self, event):
        if event.is_directory:
            return
        # Only react to actual writes — ignore open/read/close-no-write events that
        # git emits when reading refs. Without this, git log triggers a re-render loop.
        if not isinstance(event, _WRITE_EVENT_TYPES):
            return
        # Only care about ref changes, not lock files, loose objects, HEAD, config, etc.
        try:
            rel = Path(event.src_path).relative_to(self._git_dir)
        except ValueError:
            return
        rel_str = str(rel)
        if rel_str == "packed-refs" or rel_str.startswith("refs/"):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, True)


def start_watcher(
    repo_path: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> Observer:
    git_dir = Path(repo_path) / ".git"
    obs = Observer()
    handler = _Handler(queue, loop, git_dir)
    obs.schedule(handler, str(git_dir), recursive=True)
    obs.start()
    return obs
