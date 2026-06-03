"""In-browser Playwright tests against the live server."""
import json
import re

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e


# --- display ---

def test_branches_visible(browser_page: Page):
    cols = browser_page.locator(".branch-col")
    expect(cols).to_have_count(2)


def test_branch_headers(browser_page: Page):
    headers = browser_page.locator(".branch-header")
    texts = headers.all_inner_texts()
    assert "main" in texts
    assert "stable/v1" in texts


def test_commits_displayed(browser_page: Page):
    cards = browser_page.locator(".branch-col[data-branch='main'] .commit-card")
    expect(cards).to_have_count(4)  # B, C, D + Initial not shown (since A is base of stable/v1)
    # At least "Add feature B" is visible
    titles = browser_page.locator(".branch-col[data-branch='main'] .commit-card .title").all_inner_texts()
    assert any("Add feature B" in t for t in titles)


def test_matched_commits_same_color(browser_page: Page):
    # "Add feature B" on main and "[stable] Add feature B" on stable/v1 should share a color class
    main_b = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature B"
    ).first
    v1_b = browser_page.locator(".branch-col[data-branch='stable/v1'] .commit-card").filter(
        has_text="Add feature B"
    ).first

    main_class = main_b.get_attribute("class")
    v1_class = v1_b.get_attribute("class")

    # Both should have a group-N class, and the same one
    main_group = re.search(r'group-(\d+)', main_class)
    v1_group = re.search(r'group-(\d+)', v1_class)
    assert main_group is not None, f"main 'Add feature B' has no group class: {main_class}"
    assert v1_group is not None, f"stable/v1 '[stable] Add feature B' has no group class: {v1_class}"
    assert main_group.group(1) == v1_group.group(1)


def test_unmatched_commits_no_group(browser_page: Page):
    # "Add feature D" only exists on main
    card = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature D"
    ).first
    cls = card.get_attribute("class")
    assert "group-" not in cls


# --- hide / unhide ---

def test_hide_commit(browser_page: Page):
    # Hide "Add feature D"
    card = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature D"
    ).first
    hide_btn = card.locator(".btn-hide")
    hide_btn.click()
    browser_page.wait_for_timeout(600)  # allow WS update

    # Card should be gone, replaced by a hidden marker
    card_after = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature D"
    )
    expect(card_after).to_have_count(0)

    marker = browser_page.locator(".branch-col[data-branch='main'] .hidden-marker")
    expect(marker).to_have_count(1)


def test_unhide_via_overlay(browser_page: Page):
    marker = browser_page.locator(".branch-col[data-branch='main'] .hidden-marker").first
    marker.click()

    dialog = browser_page.locator("#hidden-dialog")
    expect(dialog).to_be_visible()

    # Click "show" for the hidden commit
    show_btn = dialog.locator("button", has_text="show").first
    show_btn.click()
    browser_page.wait_for_timeout(600)

    expect(dialog).not_to_be_visible()
    # Add feature D should be visible again
    card = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature D"
    )
    expect(card).to_have_count(1)


# --- diff overlay ---

def test_diff_overlay_opens(browser_page: Page):
    # Click a commit title that has a group (should have underline-dotted)
    title = browser_page.locator(
        ".branch-col[data-branch='main'] .commit-card .title.has-group"
    ).first
    title.click()
    browser_page.wait_for_timeout(500)

    dialog = browser_page.locator("#diff-dialog")
    expect(dialog).to_be_visible()

    browser_page.locator("#diff-close").click()
    expect(dialog).not_to_be_visible()


# --- error display ---

def test_error_on_invalid_operation(browser_page: Page):
    # POST a nonsense operation via JS and check the error dialog appears
    browser_page.evaluate("""
        fetch('/api/operation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({type: 'delete', sha: '0000000000000000000000000000000000000000', branch: 'main'})
        }).then(r => r.json()).then(d => {
            if (!d.success) {
                document.getElementById('error-output').textContent = d.error;
                document.getElementById('error-command').textContent = d.command || '';
                document.getElementById('error-dialog').showModal();
            }
        });
    """)
    browser_page.wait_for_timeout(1000)
    dialog = browser_page.locator("#error-dialog")
    expect(dialog).to_be_visible()

    browser_page.locator("#error-close").click()
    expect(dialog).not_to_be_visible()


# --- flush hidden ---

def test_flush_hidden_button(browser_page: Page):
    # First hide something
    card = browser_page.locator(".branch-col[data-branch='main'] .commit-card").filter(
        has_text="Add feature C"
    ).first
    if card.count() > 0:
        card.locator(".btn-hide").click()
        browser_page.wait_for_timeout(400)

    # Flush
    browser_page.locator("#flush-hidden-btn").click()
    browser_page.wait_for_timeout(600)

    # No hidden markers should remain
    markers = browser_page.locator(".hidden-marker")
    expect(markers).to_have_count(0)
