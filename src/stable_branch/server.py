import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .git_ops import GitWorktree, OpResult
from .matcher import assign_groups
from .models import Branch, Commit, Config
from .watcher import start_watcher

FRONTEND = Path(__file__).parent.parent.parent / "frontend"

_config: Config
_wt: GitWorktree
_ws_clients: set[WebSocket] = set()
_change_queue: asyncio.Queue


def _hidden_path(config: Config) -> Path:
    return Path(config.repo_path) / ".git" / "stable-branch-hidden"


def _load_hidden(config: Config) -> set[str]:
    p = _hidden_path(config)
    if p.exists():
        return set(json.loads(p.read_text()))
    return set()


def _save_hidden(config: Config, hidden: set[str]) -> None:
    _hidden_path(config).write_text(json.dumps(sorted(hidden)))


def _build_state() -> dict:
    hidden = _load_hidden(_config)
    branches: list[Branch] = []
    for bname in _config.branches:
        beginning = _config.branch_beginnings.get(bname)
        raw = _wt.get_commits(bname, since=beginning)
        commits = [
            Commit(
                sha=c["sha"],
                short_sha=c["short_sha"],
                title=c["title"],
                author=c["author"],
                timestamp=c["timestamp"],
                branch=bname,
                hidden=c["sha"] in hidden,
            )
            for c in raw
        ]
        branches.append(Branch(name=bname, commits=commits))

    groups = assign_groups(branches, _config.match_threshold, _config.match_by_author)

    return {
        "branches": [
            {
                "name": b.name,
                "commits": [
                    {
                        "sha": c.sha,
                        "short_sha": c.short_sha,
                        "title": c.title,
                        "author": c.author,
                        "timestamp": c.timestamp,
                        "group_id": c.group_id,
                        "color_index": c.color_index,
                        "hidden": c.hidden,
                    }
                    for c in b.commits
                ],
            }
            for b in branches
        ],
        "groups": [
            {"id": g.id, "color_index": g.color_index, "commit_shas": g.commit_shas}
            for g in groups
        ],
    }


async def _broadcast(payload: dict) -> None:
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


async def _watch_loop() -> None:
    DEBOUNCE = 0.2
    while True:
        await _change_queue.get()
        # drain any additional events within the debounce window
        await asyncio.sleep(DEBOUNCE)
        while not _change_queue.empty():
            _change_queue.get_nowait()
        try:
            state = _build_state()
            await _broadcast(state)
        except Exception as exc:
            await _broadcast({"error": str(exc)})


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _wt, _change_queue
    _change_queue = asyncio.Queue()
    _wt = GitWorktree(_config.repo_path)

    if _config.flush_hidden:
        _save_hidden(_config, set())

    loop = asyncio.get_event_loop()
    observer = start_watcher(_config.repo_path, _change_queue, loop)
    task = asyncio.create_task(_watch_loop())

    yield

    observer.stop()
    observer.join()
    task.cancel()
    _wt.cleanup()


def create_app(config: Config) -> FastAPI:
    global _config
    _config = config

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND / "index.html")

    @app.get("/style.css")
    async def css():
        return FileResponse(FRONTEND / "style.css")

    @app.get("/app.js")
    async def js():
        return FileResponse(FRONTEND / "app.js")

    @app.get("/api/state")
    async def state():
        return _build_state()

    @app.post("/api/operation")
    async def operation(body: dict):
        op_type = body.get("type")
        result: OpResult

        if op_type == "cherrypick":
            result = _wt.cherry_pick(body["sha"], body["target_branch"])
        elif op_type == "reorder":
            result = _wt.reorder(body["branch"], body["new_order"])
        elif op_type == "delete":
            result = _wt.delete_commit(body["branch"], body["sha"])
        elif op_type == "hide":
            hidden = _load_hidden(_config)
            hidden.add(body["sha"])
            _save_hidden(_config, hidden)
            result = OpResult(True)
        elif op_type == "unhide":
            hidden = _load_hidden(_config)
            hidden.discard(body["sha"])
            _save_hidden(_config, hidden)
            result = OpResult(True)
        else:
            return {"success": False, "error": f"Unknown operation: {op_type}"}

        if result.success:
            state = _build_state()
            await _broadcast(state)

        return {
            "success": result.success,
            "error": result.error,
            "command": result.command,
        }

    @app.get("/api/diff/{sha1}/{sha2}")
    async def diff(sha1: str, sha2: str):
        text = _wt.range_diff(sha1, sha2)
        return {"diff": text}

    @app.post("/api/hidden/flush")
    async def flush_hidden():
        _save_hidden(_config, set())
        state = _build_state()
        await _broadcast(state)
        return {"success": True}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        _ws_clients.add(ws)
        try:
            await ws.send_text(json.dumps(_build_state()))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(ws)

    return app
