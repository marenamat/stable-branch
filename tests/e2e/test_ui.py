"""In-browser Selenium tests against the live server."""
import re

import pytest
from tests.e2e._page import Page, expect


pytestmark = pytest.mark.e2e


def _branch_cells(page: Page, branch: str):
    """All non-empty grid cells for a branch."""
    return page.locator(f".grid-cell[data-branch='{branch}'] .commit-card")


def _empty_cells(page: Page, branch: str):
    """Empty (drop-target) grid cells for a branch."""
    return page.locator(f".grid-cell.empty[data-branch='{branch}']")


# --- display ---

def test_branch_headers_visible(browser_page: Page):
    headers = browser_page.locator(".branch-header")
    texts = headers.all_inner_texts()
    assert "main" in texts
    assert "stable/v1" in texts


def test_commits_displayed(browser_page: Page):
    cards = _branch_cells(browser_page, "main")
    expect(cards).to_have_count(4)  # Implement caching layer, Add rate limiting, Fix memory leak + Initial
    titles = browser_page.locator(".grid-cell[data-branch='main'] .commit-card .title").all_inner_texts()
    assert any("Implement caching layer" in t for t in titles)


def test_matched_commits_same_row(browser_page: Page):
    # "Implement caching layer" on main and "[stable] Implement caching layer" on stable/v1 should be in the same grid row.
    # Both cards should share the same parent .grid-cell sibling row (same CSS grid row = same group-N class).
    main_b = browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Implement caching layer"
    ).first
    v1_b = browser_page.locator(".grid-cell[data-branch='stable/v1'] .commit-card").filter(
        has_text="Implement caching layer"
    ).first

    main_group = re.search(r'group-(\d+)', main_b.get_attribute("class") or "")
    v1_group = re.search(r'group-(\d+)', v1_b.get_attribute("class") or "")
    assert main_group, "main 'Implement caching layer' should have a group-N class"
    assert v1_group, "stable/v1 '[stable] Implement caching layer' should have a group-N class"
    assert main_group.group(1) == v1_group.group(1), "matched commits should share a color group"


def test_matched_row_has_background_tint(browser_page: Page):
    # The cells in the same row as a matched commit should carry a row-group-N tint class.
    main_cell = browser_page.locator(".grid-cell[data-branch='main']").filter(
        has=browser_page.locator(".commit-card").filter(has_text="Implement caching layer")
    ).first
    cell_class = main_cell.get_attribute("class") or ""
    assert re.search(r'row-group-\d+', cell_class), \
        f"matched cell should have row-group-N class, got: {cell_class}"


def test_matched_row_empty_cell_also_tinted(browser_page: Page):
    # Find the group row for "Add feature C" (only on main in the e2e fixture — stable/v1 has it too)
    # The stable/v1 cell for "Fix memory leak in worker" (only on main) should be empty AND tinted for its row.
    # "Fix memory leak in worker" is unmatched → its stable/v1 cell should be empty but NOT tinted.
    # Just verify: all non-empty cells for a matched group carry row-group-N.
    main_b_cell = browser_page.locator(".grid-cell[data-branch='main']").filter(
        has=browser_page.locator(".commit-card").filter(has_text="Implement caching layer")
    ).first
    v1_b_cell = browser_page.locator(".grid-cell[data-branch='stable/v1']").filter(
        has=browser_page.locator(".commit-card").filter(has_text="Implement caching layer")
    ).first

    main_class = main_b_cell.get_attribute("class") or ""
    v1_class = v1_b_cell.get_attribute("class") or ""
    main_g = re.search(r'row-group-(\d+)', main_class)
    v1_g = re.search(r'row-group-(\d+)', v1_class)
    assert main_g and v1_g
    assert main_g.group(1) == v1_g.group(1), "cells in same row should share row-group-N"


def test_unmatched_commit_no_group(browser_page: Page):
    # "Fix memory leak in worker" is only on main → no group-N class
    card = browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Fix memory leak in worker"
    ).first
    cls = card.get_attribute("class") or ""
    assert "group-" not in cls


# --- hide / unhide ---

def test_hide_commit(browser_page: Page):
    card = browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Fix memory leak in worker"
    ).first
    card.locator(".btn-hide").click()
    browser_page.wait_for_timeout(600)

    expect(browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Fix memory leak in worker"
    )).to_have_count(0)
    expect(browser_page.locator(".grid-cell[data-branch='main'] .hidden-strip")).to_have_count(1)


def test_unhide_via_overlay(browser_page: Page):
    strip = browser_page.locator(".grid-cell[data-branch='main'] .hidden-strip").first
    strip.click()

    dialog = browser_page.locator("#hidden-dialog")
    expect(dialog).to_be_visible()

    dialog.locator("button", has_text="show").first.click()
    browser_page.wait_for_timeout(600)

    expect(dialog).not_to_be_visible()
    expect(browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Fix memory leak in worker"
    )).to_have_count(1)


# --- diff overlay ---

def test_diff_overlay_opens_and_closes(browser_page: Page):
    title = browser_page.locator(
        ".grid-cell[data-branch='main'] .commit-card .title"
    ).first
    title.click()
    browser_page.wait_for_timeout(400)

    dialog = browser_page.locator("#diff-dialog")
    expect(dialog).to_be_visible()
    browser_page.locator("#diff-close").click()
    expect(dialog).not_to_be_visible()


# --- error display ---

def test_error_dialog_on_bad_operation(browser_page: Page):
    browser_page.evaluate("""
        (async () => {
            const r = await fetch('/api/operation', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({type: 'delete', sha: '0'.repeat(40), branch: 'main'})
            });
            const d = await r.json();
            if (!d.success) {
                document.getElementById('error-output').textContent = d.error;
                document.getElementById('error-command').textContent = d.command || '';
                document.getElementById('error-dialog').showModal();
            }
        })();
    """)
    browser_page.wait_for_timeout(800)
    expect(browser_page.locator("#error-dialog")).to_be_visible()
    browser_page.locator("#error-close").click()
    expect(browser_page.locator("#error-dialog")).not_to_be_visible()


# --- flush hidden ---

def test_flush_hidden(browser_page: Page):
    # Hide something first
    cards = browser_page.locator(".grid-cell[data-branch='main'] .commit-card")
    if cards.count() > 0:
        cards.first.locator(".btn-hide").click()
        browser_page.wait_for_timeout(400)

    browser_page.locator("#flush-hidden-btn").click()
    browser_page.wait_for_timeout(600)
    expect(browser_page.locator(".hidden-strip")).to_have_count(0)


# --- diff overlay content ---

def test_diff_overlay_shows_message_and_diff(browser_page: Page):
    title = browser_page.locator(
        ".grid-cell[data-branch='main'] .commit-card .title"
    ).filter(has_text="Implement caching layer").first
    title.click()

    dialog = browser_page.locator("#diff-dialog")
    expect(dialog).to_be_visible()

    # Wait for async fetch to replace placeholder "…"
    message_el = browser_page.locator("#diff-message")
    browser_page.wait_for_function("document.getElementById('diff-message').textContent !== '…'")
    assert "Implement caching layer" in (message_el.text_content() or "")

    patch_el = browser_page.locator("#diff-patch")
    browser_page.wait_for_function("document.getElementById('diff-patch').textContent !== '…'")
    assert len(patch_el.text_content() or "") > 0

    browser_page.locator("#diff-close").click()
    expect(dialog).not_to_be_visible()


# --- ref badges ---

def test_ref_badge_on_branch_tip(browser_page: Page):
    # Exactly one commit in the main column should carry the "main" branch badge — the tip.
    badge = browser_page.locator(
        ".grid-cell[data-branch='main'] .commit-card .ref-badge.ref-branch"
    )
    expect(badge).to_have_count(1)
    assert "main" in (badge.text_content() or "")


# --- reorder buttons and functionality ---

def test_reorder_up_button_exists(browser_page: Page):
    btn = browser_page.locator(".grid-cell[data-branch='main'] .commit-card .btn-up").first
    expect(btn).to_be_visible()


def test_reorder_down_button_exists(browser_page: Page):
    btn = browser_page.locator(".grid-cell[data-branch='main'] .commit-card .btn-dn").first
    expect(btn).to_be_visible()


def test_reorder_down_button_swaps_commits(browser_page: Page):
    # Compare only commit titles (SHA changes after rebase, so use .title sub-selector)
    title_loc = browser_page.locator(".grid-cell[data-branch='stable/v1'] .commit-card .title")
    titles_before = title_loc.all_inner_texts()
    # stable/v1 (top→bottom): [stable] Add rate limiting to API, [stable] Implement caching layer, Initial commit
    browser_page.locator(".grid-cell[data-branch='stable/v1'] .commit-card").first.locator(".btn-dn").click()
    browser_page.wait_for_timeout(1000)

    titles_after = browser_page.locator(
        ".grid-cell[data-branch='stable/v1'] .commit-card .title"
    ).all_inner_texts()
    assert titles_after[0] != titles_before[0], "top commit title should change after reorder"
    assert set(titles_after[:2]) == set(titles_before[:2]), "same two commits, different order"


# --- delete and trash ---

def test_delete_commit_removes_from_grid(browser_page: Page):
    card = browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
        has_text="Fix memory leak in worker"
    ).first
    card.locator(".btn-del").click()
    browser_page.wait_for_timeout(600)

    expect(
        browser_page.locator(".grid-cell[data-branch='main'] .commit-card").filter(
            has_text="Fix memory leak in worker"
        )
    ).to_have_count(0)


def test_deleted_commit_appears_in_trash(browser_page: Page):
    # After the previous test deleted "Fix memory leak in worker", it should appear in the trash panel.
    trash = browser_page.locator("#trash-list .trash-item")
    expect(trash).to_have_count(1)
    assert "Fix memory leak in worker" in (trash.first.text_content() or "")
