import pytest
from starlette.testclient import TestClient

from stable_branch.models import Config
from stable_branch.server import create_app, _git_common_dir
from tests.conftest import git, make_commit


def _config(tmp_repo, branches=("main", "stable/v1")):
    return Config(
        repo_path=str(tmp_repo),
        branches=list(branches),
        port=8000,
    )


@pytest.fixture
def client(tmp_repo):
    app = create_app(_config(tmp_repo))
    with TestClient(app) as c:
        yield c


# --- _git_common_dir ---

def test_git_common_dir_normal_repo(tmp_repo):
    d = _git_common_dir(str(tmp_repo))
    assert d.is_dir()
    assert (d / "HEAD").exists()


def test_git_common_dir_worktree(tmp_path, tmp_repo):
    wt_path = tmp_path / "wt"
    git(tmp_repo, "worktree", "add", "--detach", str(wt_path))
    try:
        d = _git_common_dir(str(wt_path))
        assert d.is_dir()
        assert (d / "HEAD").exists()
        assert d == _git_common_dir(str(tmp_repo))
    finally:
        git(tmp_repo, "worktree", "remove", "--force", str(wt_path))


# --- /api/state structure ---

def test_state_returns_both_branches(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    names = [b["name"] for b in r.json()["branches"]]
    assert "main" in names
    assert "stable/v1" in names


def test_state_commit_has_expected_fields(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    c = main["commits"][0]
    for field in ("sha", "short_sha", "title", "author", "timestamp", "hidden", "refs"):
        assert field in c, f"missing field: {field}"


def test_state_hidden_default_false(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    assert all(not c["hidden"] for c in main["commits"])


def test_state_refs_branch_tip(client, tmp_repo):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    tip = main["commits"][0]
    ref_names = [r["name"] for r in tip["refs"]]
    assert "main" in ref_names


def test_state_refs_lightweight_tag(client, tmp_repo):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha_d = next(c["sha"] for c in main["commits"] if c["title"] == "Add D")
    git(tmp_repo, "tag", "v1.0-lw", sha_d)

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    commit_d = next(c for c in main2["commits"] if c["sha"] == sha_d)
    tag_names = [r["name"] for r in commit_d["refs"] if r["type"] == "tag"]
    assert "v1.0-lw" in tag_names


def test_state_groups_contain_backport_pair(tmp_path):
    # Use distinctive long titles so the 0.80 threshold only matches the intended pair.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test Author")
    make_commit(repo, "Initial baseline commit", "base.txt")
    sha_initial = git(repo, "rev-parse", "HEAD").stdout.strip()
    make_commit(repo, "Fix race condition in session manager", "fix.txt")
    sha_main = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-b", "stable/v1", sha_initial)
    make_commit(repo, "[stable] Fix race condition in session manager", "fix.txt")
    sha_v1 = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "main")

    config = Config(repo_path=str(repo), branches=["main", "stable/v1"], port=8000)
    app = create_app(config)
    with TestClient(app) as c:
        data = c.get("/api/state").json()

    group_sha_sets = [frozenset(g["commit_shas"]) for g in data["groups"]]
    assert any(sha_main in s and sha_v1 in s for s in group_sha_sets)


# --- hide / unhide ---

def test_hide_marks_commit_hidden(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha = main["commits"][0]["sha"]

    r = client.post("/api/operation", json={"type": "hide", "sha": sha, "branch": "main"})
    assert r.json()["success"]

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    commit = next(c for c in main2["commits"] if c["sha"] == sha)
    assert commit["hidden"]


def test_unhide_clears_hidden_flag(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha = main["commits"][0]["sha"]

    client.post("/api/operation", json={"type": "hide", "sha": sha, "branch": "main"})
    client.post("/api/operation", json={"type": "unhide", "sha": sha, "branch": "main"})

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    commit = next(c for c in main2["commits"] if c["sha"] == sha)
    assert not commit["hidden"]


def test_flush_hidden_clears_all(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha = main["commits"][0]["sha"]

    client.post("/api/operation", json={"type": "hide", "sha": sha, "branch": "main"})
    client.post("/api/hidden/flush")

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    commit = next(c for c in main2["commits"] if c["sha"] == sha)
    assert not commit["hidden"]


# --- /api/commit/{sha} ---

def test_commit_detail_message(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha_e = next(c["sha"] for c in main["commits"] if c["title"] == "Add E")

    r = client.get(f"/api/commit/{sha_e}")
    assert r.status_code == 200
    detail = r.json()
    assert "Add E" in detail["message"]
    assert "diff" in detail


def test_commit_detail_diff_contains_added_file(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha_b = next(c["sha"] for c in main["commits"] if c["title"] == "Add B")

    detail = client.get(f"/api/commit/{sha_b}").json()
    assert "+Add B" in detail["diff"]


# --- unknown operation ---

def test_unknown_operation_returns_error(client):
    r = client.post("/api/operation", json={"type": "bogus"})
    data = r.json()
    assert not data["success"]
    assert "bogus" in data["error"]
