import argparse
import os
import signal
import socket
import subprocess
import sys
import tomllib
import webbrowser
from pathlib import Path

import uvicorn

from .models import Config
from .server import create_app

# Set by SIGUSR1 (restart request from /api/restart); checked after uvicorn exits.
_restart_requested = False


def _sigusr1_handler(signum, frame):
    global _restart_requested
    _restart_requested = True
    os.kill(os.getpid(), signal.SIGTERM)


def _branch_exists(repo_path: str, branch: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo_path, capture_output=True,
    )
    return r.returncode == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    p = argparse.ArgumentParser(
        prog="stable-branch",
        description="Browser tool for viewing and managing stable branches",
    )
    p.add_argument("repo", nargs="?", help="Path to git repository")
    p.add_argument("branches", nargs="*", help="Branch names to display")
    p.add_argument("--config", metavar="FILE", default="stable-branch.toml",
                   help="TOML config file (default: stable-branch.toml)")
    p.add_argument("--port", type=int, help="Port to listen on (default: random free port)")
    p.add_argument("--match-threshold", type=float, dest="match_threshold",
                   help="Commit similarity threshold 0–1 (default: 0.80)")
    p.add_argument("--match-by-author", action="store_true", dest="match_by_author",
                   help="Require same author for commit matching")
    p.add_argument("--beginning", action="append", metavar="BRANCH=REF", dest="beginnings",
                   help="Branch beginning as 'branch=tag-or-sha' (repeatable)")
    p.add_argument("--flush-hidden", action="store_true", dest="flush_hidden",
                   help="Clear persisted hidden commits on startup")
    p.add_argument("--no-open", action="store_true", dest="no_open",
                   help="Don't open browser tab automatically")
    p.add_argument("--hide-merges", action="store_true", dest="hide_merges",
                   help="Auto-hide merge commits (show as strips)")
    p.add_argument("--issue-url", dest="issue_url", metavar="URL",
                   help="URL prefix for #N issue links (e.g. https://github.com/org/repo/issues/)")
    p.add_argument("--remote", action="append", dest="remotes", metavar="REMOTE",
                   help="Show remote-tracking refs for this remote (repeatable)")
    args = p.parse_args()

    cfg: dict = {}
    toml_path = Path(args.config)
    if toml_path.exists():
        cfg = _load_toml(toml_path)

    repo = args.repo or cfg.get("repo")
    branches = args.branches or cfg.get("branches", [])

    if not repo:
        p.error("repo is required (as argument or in config file)")
    if not branches:
        p.error("at least one branch is required")

    match_cfg = cfg.get("match", {})
    filter_cfg = cfg.get("filter", {})
    beginnings_cfg: dict[str, str] = cfg.get("beginnings", {})
    if args.beginnings:
        for item in args.beginnings:
            if "=" not in item:
                p.error(f"--beginning expects BRANCH=REF, got: {item!r}")
            k, v = item.split("=", 1)
            beginnings_cfg[k] = v

    port = args.port or cfg.get("port") or _free_port()

    config = Config(
        repo_path=str(Path(repo).resolve()),
        branches=branches,
        port=port,
        match_threshold=(
            args.match_threshold
            if args.match_threshold is not None
            else match_cfg.get("threshold", 0.80)
        ),
        match_by_author=args.match_by_author or match_cfg.get("by_author", False),
        branch_beginnings=beginnings_cfg,
        flush_hidden=args.flush_hidden,
        open_browser=cfg.get("open_browser", True) and not args.no_open,
        hide_merges=args.hide_merges or cfg.get("hide_merges", False),
        hide_if=filter_cfg.get("hide_if", {}),
        highlight_if=filter_cfg.get("highlight_if", {}),
        issue_url=args.issue_url or cfg.get("issue_url"),
        relevant_remotes=args.remotes or cfg.get("relevant_remotes", []),
    )

    missing = [b for b in config.branches if not _branch_exists(config.repo_path, b)]
    if missing:
        print("error: the following branches were not found in the repository:", file=sys.stderr)
        for b in missing:
            print(f"  {b}", file=sys.stderr)
        sys.exit(1)

    signal.signal(signal.SIGUSR1, _sigusr1_handler)

    url = f"http://127.0.0.1:{config.port}"
    branch_src = "command line" if args.branches else f"config {toml_path}" if toml_path.exists() else "command line"
    print(f"stable-branch listening on {url}", flush=True)
    print(f"  repo: {config.repo_path}", flush=True)
    print(f"  branches ({branch_src}): {', '.join(config.branches)}", flush=True)

    first_run = True
    while True:
        global _restart_requested
        _restart_requested = False
        app = create_app(config)
        if first_run and config.open_browser:
            webbrowser.open(url)
            first_run = False
        try:
            uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="warning")
        except SystemExit:
            pass
        if _restart_requested:
            print("restarting…", flush=True)
        else:
            break


if __name__ == "__main__":
    main()
