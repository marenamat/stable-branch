import asyncio
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = loop

    def on_any_event(self, event):
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, True)


def start_watcher(
    repo_path: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> Observer:
    git_dir = Path(repo_path) / ".git"
    obs = Observer()
    handler = _Handler(queue, loop)
    obs.schedule(handler, str(git_dir), recursive=True)
    obs.start()
    return obs
