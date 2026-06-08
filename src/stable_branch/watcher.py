import asyncio
import subprocess
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


def _git_common_dir(repo_path: str) -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        d = Path(r.stdout.strip())
        return (Path(repo_path) / d).resolve()
    return Path(repo_path) / ".git"


def _is_relevant(path: str, git_dir: Path) -> bool:
    try:
        rel = str(Path(path).relative_to(git_dir))
    except ValueError:
        return False
    return rel == "packed-refs" or rel.startswith("refs/")


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, git_dir: Path):
        self._queue = queue
        self._loop = loop
        self._git_dir = git_dir

    def on_any_event(self, event):
        if event.is_directory:
            return
        if not isinstance(event, _WRITE_EVENT_TYPES):
            return
        paths = [event.src_path]
        if isinstance(event, FileMovedEvent):
            paths.append(event.dest_path)
        if any(_is_relevant(p, self._git_dir) for p in paths):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, True)


def start_watcher(
    repo_path: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> Observer:
    git_dir = _git_common_dir(repo_path)
    obs = Observer()
    handler = _Handler(queue, loop, git_dir)
    obs.schedule(handler, str(git_dir), recursive=True)
    obs.start()
    return obs
