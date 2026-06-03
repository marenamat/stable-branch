import subprocess
from pathlib import Path

import pytest

from tests.conftest import git, make_commit
from stable_branch.git_ops import GitWorktree


@pytest.fixture
def wt(tmp_repo):
    worktree = GitWorktree(str(tmp_repo))
    yield worktree
    worktree.cleanup()


# --- commit reading ---

def test_get_commits_main(wt, tmp_repo):
    commits = wt.get_commits("main")
    titles = [c["title"] for c in commits]
    assert titles[0] == "Add E"
    assert "Initial commit" in titles


def test_get_commits_since(wt, tmp_repo):
    all_commits = wt.get_commits("main")
    base_sha = all_commits[-1]["sha"]  # oldest = Initial commit
    commits = wt.get_commits("main", since=base_sha)
    titles = [c["title"] for c in commits]
    assert "Initial commit" not in titles
    assert "Add B" in titles


def test_get_commits_stable(wt, tmp_repo):
    commits = wt.get_commits("stable/v1")
    titles = [c["title"] for c in commits]
    assert "[stable] Add C" in titles
    assert "[stable] Add B" in titles


def test_commit_fields(wt, tmp_repo):
    commits = wt.get_commits("main")
    c = commits[0]
    assert len(c["sha"]) == 40
    assert len(c["short_sha"]) == 8
    assert c["title"]
    assert c["author"] == "Test Author"
    assert isinstance(c["timestamp"], int)


# --- cherry-pick ---

def test_cherry_pick_success(wt, tmp_repo):
    main_commits = wt.get_commits("main")
    sha_e = next(c["sha"] for c in main_commits if c["title"] == "Add E")

    result = wt.cherry_pick(sha_e, "stable/v1")
    assert result.success

    v1_commits = wt.get_commits("stable/v1")
    assert any(c["title"] == "Add E" for c in v1_commits)


def test_cherry_pick_conflict(wt, tmp_repo):
    # Create a conflicting commit on stable/v1 that touches b.txt differently
    git(tmp_repo, "checkout", "stable/v1")
    (tmp_repo / "b.txt").write_text("conflict content\n")
    git(tmp_repo, "add", "b.txt")
    git(tmp_repo, "commit", "-m", "Change b differently")
    git(tmp_repo, "checkout", "main")

    main_commits = wt.get_commits("main")
    sha_b = next(c["sha"] for c in main_commits if c["title"] == "Add B")

    # cherry-picking Add B onto stable/v1 should conflict (b.txt already exists with diff content)
    # Actually since our fixture already has b.txt on both, let's create a conflict manually:
    # Add a commit on main that changes the same file differently
    git(tmp_repo, "checkout", "main")
    (tmp_repo / "b.txt").write_text("main version\n")
    git(tmp_repo, "add", "b.txt")
    git(tmp_repo, "commit", "-m", "Change b on main")
    sha_conflict = git(tmp_repo, "rev-parse", "HEAD").stdout.strip()

    # This cherry-pick onto stable/v1 (which has "conflict content") should fail
    result = wt.cherry_pick(sha_conflict, "stable/v1")
    assert not result.success
    assert result.command
    assert result.error

    # Worktree should be clean after rollback
    r = subprocess.run(["git", "status", "--porcelain"], cwd=wt.wt, capture_output=True, text=True)
    assert r.stdout.strip() == ""


# --- delete ---

def test_delete_commit(wt, tmp_repo):
    v1_before = wt.get_commits("stable/v1")
    sha_to_del = next(c["sha"] for c in v1_before if c["title"] == "[stable] Add C")

    result = wt.delete_commit("stable/v1", sha_to_del)
    assert result.success

    v1_after = wt.get_commits("stable/v1")
    assert not any(c["sha"] == sha_to_del for c in v1_after)
    assert any(c["title"] == "[stable] Add B" for c in v1_after)


def test_delete_nonexistent_commit_fails(wt, tmp_repo):
    result = wt.delete_commit("stable/v1", "0" * 40)
    assert not result.success


# --- reorder ---

def test_reorder_commits(wt, tmp_repo):
    # Add two more commits to stable/v2 so we have something to reorder
    git(tmp_repo, "checkout", "stable/v2")
    make_commit(tmp_repo, "Extra commit X", "x.txt")
    make_commit(tmp_repo, "Extra commit Y", "y.txt")
    git(tmp_repo, "checkout", "main")

    commits_before = wt.get_commits("stable/v2")
    # newest-first: Y, X, BACKPORT Add B, Initial
    # reorder: swap only the top two (Y ↔ X); Initial is the root and not part of the range
    top_two = [commits_before[0]["sha"], commits_before[1]["sha"]]
    new_order = [top_two[1], top_two[0]]  # oldest-in-range^ = commits_before[2] (BACKPORT)

    result = wt.reorder("stable/v2", new_order)
    assert result.success

    commits_after = wt.get_commits("stable/v2")
    # titles should have swapped for top two
    assert commits_after[0]["title"] == commits_before[1]["title"]
    assert commits_after[1]["title"] == commits_before[0]["title"]


# --- worktree lifecycle ---

def test_cleanup(tmp_repo):
    wt2 = GitWorktree(str(tmp_repo))
    assert wt2.wt.exists()
    wt2.cleanup()
    assert not wt2.wt.exists()
