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


# --- commit_message ---

def test_commit_message(wt, tmp_repo):
    commits = wt.get_commits("main")
    sha_e = next(c["sha"] for c in commits if c["title"] == "Add E")
    msg = wt.commit_message(sha_e)
    assert "Add E" in msg


def test_commit_message_full_body(wt, tmp_repo):
    git(tmp_repo, "checkout", "main")
    (tmp_repo / "msg_test.txt").write_text("content\n")
    git(tmp_repo, "add", "msg_test.txt")
    git(tmp_repo, "commit", "-m", "Subject line\n\nBody paragraph here.")
    commits = wt.get_commits("main")
    sha = commits[0]["sha"]
    msg = wt.commit_message(sha)
    assert "Subject line" in msg
    assert "Body paragraph here." in msg


# --- commit_diff ---

def test_commit_diff_contains_added_content(wt, tmp_repo):
    commits = wt.get_commits("main")
    sha_b = next(c["sha"] for c in commits if c["title"] == "Add B")
    diff = wt.commit_diff(sha_b)
    assert "+Add B" in diff


def test_commit_diff_is_string(wt, tmp_repo):
    commits = wt.get_commits("main")
    sha = commits[0]["sha"]
    diff = wt.commit_diff(sha)
    assert isinstance(diff, str)
    assert len(diff) > 0


# --- refs_by_sha ---

def test_refs_by_sha_branch_tip(wt, tmp_repo):
    main_tip = git(tmp_repo, "rev-parse", "main").stdout.strip()
    refs = wt.refs_by_sha({main_tip})
    assert main_tip in refs
    branch_refs = [r for r in refs[main_tip] if r["type"] == "branch"]
    assert any(r["name"] == "main" for r in branch_refs)


def test_refs_by_sha_lightweight_tag(wt, tmp_repo):
    commits = wt.get_commits("main")
    sha_d = next(c["sha"] for c in commits if c["title"] == "Add D")
    git(tmp_repo, "tag", "v1.0-lw", sha_d)
    refs = wt.refs_by_sha({sha_d})
    assert sha_d in refs
    tag_refs = [r for r in refs[sha_d] if r["type"] == "tag"]
    assert any(r["name"] == "v1.0-lw" for r in tag_refs)


def test_refs_by_sha_annotated_tag(wt, tmp_repo):
    commits = wt.get_commits("main")
    sha_c = next(c["sha"] for c in commits if c["title"] == "Add C")
    git(tmp_repo, "tag", "-a", "v0.9", sha_c, "-m", "Release v0.9")
    refs = wt.refs_by_sha({sha_c})
    assert sha_c in refs
    tag_refs = [r for r in refs[sha_c] if r["type"] == "tag"]
    assert any(r["name"] == "v0.9" for r in tag_refs)


def test_refs_by_sha_unknown_sha(wt, tmp_repo):
    refs = wt.refs_by_sha({"0" * 40})
    assert refs == {}


def test_refs_by_sha_multiple_shas(wt, tmp_repo):
    main_tip = git(tmp_repo, "rev-parse", "main").stdout.strip()
    v1_tip = git(tmp_repo, "rev-parse", "stable/v1").stdout.strip()
    refs = wt.refs_by_sha({main_tip, v1_tip})
    main_names = [r["name"] for r in refs.get(main_tip, [])]
    v1_names = [r["name"] for r in refs.get(v1_tip, [])]
    assert "main" in main_names
    assert "stable/v1" in v1_names


# --- worktree lifecycle ---

def test_cleanup(tmp_repo):
    wt2 = GitWorktree(str(tmp_repo))
    assert wt2.wt.exists()
    wt2.cleanup()
    assert not wt2.wt.exists()
