import asyncio
import threading
import time

import pytest
import uvicorn

pytest.importorskip("playwright", reason="playwright not installed; skip e2e tests")

from stable_branch.models import Config
from stable_branch.server import create_app
from tests.conftest import git, make_commit


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="module")
def e2e_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("e2e") / "repo"
    repo.mkdir()

    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test Author")

    sha_a = make_commit(repo, "Initial commit", "base.txt")
    make_commit(repo, "Add feature B", "b.txt")
    make_commit(repo, "Add feature C", "c.txt")
    make_commit(repo, "Add feature D", "d.txt")

    git(repo, "checkout", "-b", "stable/v1", sha_a)
    make_commit(repo, "[stable] Add feature B", "b.txt")
    make_commit(repo, "[stable] Add feature C", "c.txt")

    git(repo, "checkout", "main")
    return repo


@pytest.fixture(scope="module")
def live_server(e2e_repo):
    config = Config(
        repo_path=str(e2e_repo),
        branches=["main", "stable/v1"],
        port=18765,
    )
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18765, log_level="error"))

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    import socket
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    yield "http://127.0.0.1:18765"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_page(playwright, live_server):
    browser = playwright.chromium.launch()
    page = browser.new_page()
    page.goto(live_server)
    page.wait_for_selector(".branch-col", timeout=5000)
    yield page
    browser.close()
