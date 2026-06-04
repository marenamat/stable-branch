import os
import shutil
import socket
import threading
import time

import pytest
import uvicorn
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from stable_branch.models import Config
from stable_branch.server import create_app
from tests.conftest import git, make_commit
from tests.e2e._page import Page  # noqa: F401 — re-exported for test type hints


def _find_chromium() -> str | None:
    candidates = [
        "/usr/bin/chromium-browser",   # Alpine Linux
        "/usr/bin/chromium",           # some Debian/Ubuntu builds
        "/usr/bin/google-chrome",      # Ubuntu CI runners
        "/usr/bin/google-chrome-stable",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def e2e_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("e2e") / "repo"
    repo.mkdir()

    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test Author")

    sha_a = make_commit(repo, "Initial commit", "base.txt")
    make_commit(repo, "Implement caching layer", "cache.txt")
    make_commit(repo, "Add rate limiting to API", "ratelimit.txt")
    make_commit(repo, "Fix memory leak in worker", "worker.txt")

    git(repo, "checkout", "-b", "stable/v1", sha_a)
    make_commit(repo, "[stable] Implement caching layer", "cache.txt")
    make_commit(repo, "[stable] Add rate limiting to API", "ratelimit.txt")

    git(repo, "checkout", "main")
    return repo


@pytest.fixture(scope="module")
def live_server(e2e_repo):
    port = _free_port()
    config = Config(
        repo_path=str(e2e_repo),
        branches=["main", "stable/v1"],
        port=port,
    )
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_page(live_server):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    chromium = _find_chromium()
    if chromium:
        opts.binary_location = chromium

    drv = webdriver.Chrome(service=Service(), options=opts)
    drv.get(live_server)
    # Wait for commit cards (not just headers) so the full initial render is done
    WebDriverWait(drv, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".commit-card"))
    )
    # Let any watcher events from worktree creation settle before tests start
    time.sleep(1.5)

    page = Page(drv)
    yield page
    drv.quit()
