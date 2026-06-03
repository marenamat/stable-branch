import argparse
import sys
import tomllib
import webbrowser
from pathlib import Path

import uvicorn

from .models import Config
from .server import create_app


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
    p.add_argument("--port", type=int, help="Port to listen on (default: 8000)")
    p.add_argument("--match-threshold", type=float, dest="match_threshold",
                   help="Commit similarity threshold 0–1 (default: 0.80)")
    p.add_argument("--match-by-author", action="store_true", dest="match_by_author",
                   help="Require same author for commit matching")
    p.add_argument("--beginning", action="append", metavar="BRANCH=REF", dest="beginnings",
                   help="Branch beginning as 'branch=tag-or-sha' (repeatable)")
    p.add_argument("--flush-hidden", action="store_true", dest="flush_hidden",
                   help="Clear persisted hidden commits on startup")
    p.add_argument("--open", action="store_true", dest="open_browser",
                   help="Open browser tab automatically")
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
    beginnings_cfg: dict[str, str] = cfg.get("beginnings", {})
    if args.beginnings:
        for item in args.beginnings:
            if "=" not in item:
                p.error(f"--beginning expects BRANCH=REF, got: {item!r}")
            k, v = item.split("=", 1)
            beginnings_cfg[k] = v

    config = Config(
        repo_path=str(Path(repo).resolve()),
        branches=branches,
        port=args.port or cfg.get("port", 8000),
        match_threshold=(
            args.match_threshold
            if args.match_threshold is not None
            else match_cfg.get("threshold", 0.80)
        ),
        match_by_author=args.match_by_author or match_cfg.get("by_author", False),
        branch_beginnings=beginnings_cfg,
        flush_hidden=args.flush_hidden,
        open_browser=args.open_browser,
    )

    app = create_app(config)

    url = f"http://127.0.0.1:{config.port}"
    branch_src = "command line" if args.branches else f"config {toml_path}" if toml_path.exists() else "command line"
    print(f"stable-branch listening on {url}", flush=True)
    print(f"  repo: {config.repo_path}", flush=True)
    print(f"  branches ({branch_src}): {', '.join(config.branches)}", flush=True)
    if config.open_browser:
        webbrowser.open(url)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=config.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
