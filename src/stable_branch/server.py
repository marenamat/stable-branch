import asyncio
import json
import os
import re
import signal
import subprocess
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
_shutdown_task: asyncio.Task | None = None
_IDLE_SHUTDOWN_SECS = 30


def _git_common_dir(repo_path: str) -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        d = Path(r.stdout.strip())
        return (Path(repo_path) / d).resolve()
    return Path(repo_path) / ".git"


def _hidden_path(config: Config) -> Path:
    return _git_common_dir(config.repo_path) / "stable-branch-hidden"


def _shown_path(config: Config) -> Path:
    return _git_common_dir(config.repo_path) / "stable-branch-shown"


def _load_hidden(config: Config) -> set[str]:
    p = _hidden_path(config)
    if p.exists():
        return set(json.loads(p.read_text()))
    return set()


def _save_hidden(config: Config, hidden: set[str]) -> None:
    _hidden_path(config).write_text(json.dumps(sorted(hidden)))


def _load_shown(config: Config) -> set[str]:
    p = _shown_path(config)
    if p.exists():
        return set(json.loads(p.read_text()))
    return set()


def _save_shown(config: Config, shown: set[str]) -> None:
    _shown_path(config).write_text(json.dumps(sorted(shown)))


_HEADER_RE = re.compile(r'^([A-Za-z][A-Za-z0-9_-]*):\s*(.+)$', re.MULTILINE)
_ISSUE_RE = re.compile(r'#(\d+)')


def _parse_headers(body: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2).strip() for m in _HEADER_RE.finditer(body)}


def _add_pre_beginning_ghosts(branches: list[Branch], config: Config) -> None:
    """Inject pre_beginning ghost commits into branches that have a configured beginning.

    For branch B with beginning SHA `b`, any commit C from another branch that is a
    git ancestor of `b` (i.e., already present in B's history before the cutoff) is
    added to B's commit list marked pre_beginning=True so the grid can display it dimmed.
    Commits already explicitly listed in B are not duplicated.
    """
    if not config.branch_beginnings:
        return

    # Collect all normal commits across all branches by SHA
    all_commits_by_sha: dict[str, Commit] = {}
    for branch in branches:
        for c in branch.commits:
            all_commits_by_sha[c.sha] = c

    for branch in branches:
        beginning = config.branch_beginnings.get(branch.name)
        if not beginning:
            continue
        existing_shas = {c.sha for c in branch.commits}
        ancestors = _wt.ancestry_shas(beginning)
        ghosts: list[Commit] = []
        for sha, commit in all_commits_by_sha.items():
            if sha not in existing_shas and sha in ancestors:
                ghost = Commit(
                    sha=commit.sha,
                    short_sha=commit.short_sha,
                    title=commit.title,
                    author=commit.author,
                    timestamp=commit.timestamp,
                    branch=branch.name,
                    hidden=commit.hidden,
                    is_merge=commit.is_merge,
                    body=commit.body,
                    highlight_index=commit.highlight_index,
                    issue_refs=commit.issue_refs,
                    pre_beginning=True,
                )
                ghosts.append(ghost)
        branch.commits.extend(ghosts)


def _build_state() -> dict:
    hidden = _load_hidden(_config)
    shown = _load_shown(_config)
    branches: list[Branch] = []
    for bname in _config.branches:
        beginning = _config.branch_beginnings.get(bname)
        raw = _wt.get_commits(bname, since=beginning)
        commits = []
        for c in raw:
            headers = _parse_headers(c["body"])

            auto_hide = (_config.hide_merges and c["is_merge"]) or any(
                (headers.get(k.lower()) or "").lower() in [v.lower() for v in vals]
                for k, vals in _config.hide_if.items()
            )
            is_hidden = c["sha"] in hidden or (auto_hide and c["sha"] not in shown)

            highlight_index = None
            for i, (k, vals) in enumerate(_config.highlight_if.items()):
                if (headers.get(k.lower()) or "").lower() in [v.lower() for v in vals]:
                    highlight_index = i % 8
                    break

            seen: set[str] = set()
            issue_refs = [
                n for n in _ISSUE_RE.findall(c["body"])
                if not (n in seen or seen.add(n))  # type: ignore[func-returns-value]
            ]

            commits.append(Commit(
                sha=c["sha"],
                short_sha=c["short_sha"],
                title=c["title"],
                author=c["author"],
                timestamp=c["timestamp"],
                committer_timestamp=c["committer_timestamp"],
                branch=bname,
                hidden=is_hidden,
                is_merge=c["is_merge"],
                body=c["body"],
                highlight_index=highlight_index,
                issue_refs=issue_refs,
            ))
        branches.append(Branch(name=bname, commits=commits))

    # For branches with a configured beginning, show commits from other branches that
    # are ancestors of that beginning as dimmed "pre_beginning" ghost entries.
    _add_pre_beginning_ghosts(branches, _config)

    groups = assign_groups(branches, _config.match_threshold, _config.match_by_author)

    all_shas = {c.sha for b in branches for c in b.commits}
    refs = _wt.refs_by_sha(all_shas, _config.relevant_remotes or None)

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
                        "committer_timestamp": c.committer_timestamp,
                        "group_id": c.group_id,
                        "color_index": c.color_index,
                        "hidden": c.hidden,
                        "is_merge": c.is_merge,
                        "highlight_index": c.highlight_index,
                        "issue_refs": c.issue_refs,
                        "pre_beginning": c.pre_beginning,
                        "refs": refs.get(c.sha, []),
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
        "config": {
            "issue_url": _config.issue_url,
        },
    }


async def _broadcast(payload: dict) -> None:
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


async def _idle_shutdown() -> None:
    await asyncio.sleep(_IDLE_SHUTDOWN_SECS)
    print(f"All clients gone for {_IDLE_SHUTDOWN_SECS}s — shutting down.", flush=True)
    os.kill(os.getpid(), signal.SIGTERM)


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

    if _shutdown_task and not _shutdown_task.done():
        _shutdown_task.cancel()
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
            shown = _load_shown(_config)
            shown.discard(body["sha"])
            _save_shown(_config, shown)
            result = OpResult(True)
        elif op_type == "unhide":
            hidden = _load_hidden(_config)
            hidden.discard(body["sha"])
            _save_hidden(_config, hidden)
            shown = _load_shown(_config)
            shown.add(body["sha"])
            _save_shown(_config, shown)
            result = OpResult(True)
        elif op_type == "amend":
            amendments = body.get("amendments", [])
            new_message = body.get("message") or None
            new_author = body.get("author") or None
            result = OpResult(True)
            for a in amendments:
                result = _wt.amend_commit(a["branch"], a["sha"], new_message, new_author)
                if not result.success:
                    break
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

    @app.get("/api/commit/{sha}")
    async def commit_detail(sha: str):
        return {
            "message": _wt.commit_message(sha),
            "diff": _wt.commit_diff(sha),
        }

    @app.get("/api/diff/{sha1}/{sha2}")
    async def diff(sha1: str, sha2: str):
        text = _wt.range_diff(sha1, sha2)
        return {"diff": text}

    @app.post("/api/restart")
    async def restart():
        async def _do_restart():
            await asyncio.sleep(0.2)
            os.kill(os.getpid(), signal.SIGUSR1)
        asyncio.create_task(_do_restart())
        return {"ok": True}

    @app.post("/api/hidden/flush")
    async def flush_hidden():
        _save_hidden(_config, set())
        state = _build_state()
        await _broadcast(state)
        return {"success": True}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        global _shutdown_task
        await ws.accept()
        if _shutdown_task and not _shutdown_task.done():
            _shutdown_task.cancel()
            _shutdown_task = None
        _ws_clients.add(ws)
        try:
            await ws.send_text(json.dumps(_build_state()))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(ws)
            if not _ws_clients:
                _shutdown_task = asyncio.create_task(_idle_shutdown())

    return app
