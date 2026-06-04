"""E2E tests for auto-hide (merges + headers), highlight, and issue link features."""
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
from tests.conftest import git, make_commit, make_merge_commit
from tests.e2e._page import Page, expect
from tests.e2e.conftest import _find_chromium, _free_port


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def filtered_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("filtered") / "repo"
    repo.mkdir()

    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test Author")

    sha_init = make_commit(repo, "Initial commit", "base.txt")
    make_commit(repo, "Add normal feature", "normal.txt")
    make_commit(repo, "Add experimental feature", "exp.txt",
                body="Character: experimental\n\nThis one should be auto-hidden.")
    make_commit(repo, "Critical fix", "crit.txt",
                body="Priority: high\n\nFixes #7\n\nSee also #12")

    # Create side branch and merge it back so we get a merge commit
    git(repo, "checkout", "-b", "side-branch", sha_init)
    make_commit(repo, "Side branch change", "side.txt")
    git(repo, "checkout", "main")
    make_merge_commit(repo, "side-branch", "Merge side-branch into main")

    git(repo, "checkout", "-b", "stable/v1", sha_init)
    make_commit(repo, "[stable] Add normal feature", "normal.txt")

    git(repo, "checkout", "main")
    return repo


@pytest.fixture(scope="module")
def filtered_server(filtered_repo):
    port = _free_port()
    config = Config(
        repo_path=str(filtered_repo),
        branches=["main", "stable/v1"],
        port=port,
        hide_merges=True,
        hide_if={"Character": ["experimental"]},
        highlight_if={"Priority": ["high"]},
        issue_url="https://example.com/issues/",
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
def filtered_page(filtered_server):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    chromium = _find_chromium()
    if chromium:
        opts.binary_location = chromium

    drv = webdriver.Chrome(service=Service(), options=opts)
    drv.get(filtered_server)
    WebDriverWait(drv, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".commit-card"))
    )
    time.sleep(1.5)

    page = Page(drv)
    yield page
    drv.quit()


# --- merge auto-hide ---

def test_merge_commit_not_visible_as_card(filtered_page: Page):
    cards = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card")
    titles = cards.all_inner_texts()
    assert not any("Merge side-branch" in t for t in titles)


def test_merge_shows_as_strip(filtered_page: Page):
    expect(filtered_page.locator(".grid-cell[data-branch='main'] .hidden-strip")).to_have_count(2)


# --- header-based auto-hide ---

def test_header_hide_commit_not_visible_as_card(filtered_page: Page):
    cards = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card")
    titles = cards.all_inner_texts()
    assert not any("Add experimental feature" in t for t in titles)


# --- highlight ---

def test_highlight_class_applied(filtered_page: Page):
    card = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Critical fix"
    ).first
    cls = card.get_attribute("class") or ""
    assert "highlight-0" in cls, f"Expected highlight-0 on Critical fix card, got: {cls}"


def test_non_highlighted_card_has_no_highlight_class(filtered_page: Page):
    card = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Add normal feature"
    ).first
    cls = card.get_attribute("class") or ""
    assert "highlight-" not in cls


# --- issue badges ---

def test_issue_badge_rendered(filtered_page: Page):
    card = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Critical fix"
    ).first
    badge = card.locator(".ref-issue")
    expect(badge).to_have_count(2)  # #7 and #12


def test_issue_badge_text(filtered_page: Page):
    card = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Critical fix"
    ).first
    texts = card.locator(".ref-issue").all_inner_texts()
    assert "#7" in texts
    assert "#12" in texts


def test_issue_badge_href(filtered_page: Page):
    card = filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Critical fix"
    ).first
    badge7 = card.locator(".ref-issue").filter(has_text="#7").first
    href = badge7.get_attribute("href")
    assert href == "https://example.com/issues/7"


def test_no_issue_badges_without_issue_url(filtered_page: Page):
    # stable/v1 is served by the same server with issue_url set, but
    # "[stable] Add normal feature" has no issue refs in its body
    card = filtered_page.locator(".grid-cell[data-branch='stable/v1'] .commit-card").filter(
        has_text="Add normal feature"
    ).first
    assert card.locator(".ref-issue").count() == 0


# --- unhide auto-hidden via dialog ---

def test_unhide_auto_hidden_via_dialog(filtered_page: Page):
    # Click the specific hidden-strip for "Add experimental feature" (matched by title attr)
    strip = filtered_page.locator(
        ".grid-cell[data-branch='main'] .hidden-strip[title*='Add experimental feature']"
    )
    strip.click()

    dialog = filtered_page.locator("#hidden-dialog")
    expect(dialog).to_be_visible()

    dialog.locator("button", has_text="show").first.click()
    filtered_page.wait_for_timeout(800)

    expect(dialog).not_to_be_visible()
    # Commit should now appear as a card
    expect(
        filtered_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
            has_text="Add experimental feature"
        )
    ).to_have_count(1)
