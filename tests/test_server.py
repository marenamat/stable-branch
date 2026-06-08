import pytest
from starlette.testclient import TestClient

from stable_branch.models import Config
from stable_branch.server import create_app, _git_common_dir, _load_shown
from tests.conftest import git, make_commit, make_merge_commit


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
    for field in ("sha", "short_sha", "title", "author", "timestamp", "committer_timestamp", "hidden", "is_merge", "refs"):
        assert field in c, f"missing field: {field}"


def test_is_merge_field_in_state(tmp_repo):
    git(tmp_repo, "checkout", "-b", "feat-is-merge", "main")
    make_commit(tmp_repo, "Feature is-merge", "feature_ism.txt")
    git(tmp_repo, "checkout", "main")
    make_merge_commit(tmp_repo, "feat-is-merge", "Merge feat-is-merge")

    cfg = Config(repo_path=str(tmp_repo), branches=["main"])
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    merge = next(cm for cm in main["commits"] if cm["title"] == "Merge feat-is-merge")
    regular = next(cm for cm in main["commits"] if cm["title"] == "Add E")
    assert merge["is_merge"] is True
    assert regular["is_merge"] is False


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


# --- hide_merges ---

def test_hide_merges_shows_merge_as_strip(tmp_repo):
    git(tmp_repo, "checkout", "-b", "feature-m", "main")
    make_commit(tmp_repo, "Feature M", "feature_m.txt")
    git(tmp_repo, "checkout", "main")
    make_merge_commit(tmp_repo, "feature-m", "Merge feature-m")

    cfg = Config(repo_path=str(tmp_repo), branches=["main"], hide_merges=True)
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    merge = next((cm for cm in main["commits"] if cm["title"] == "Merge feature-m"), None)
    assert merge is not None, "merge commit should still appear in state (as hidden strip)"
    assert merge["hidden"] is True


def test_hide_merges_false_does_not_hide(tmp_repo):
    git(tmp_repo, "checkout", "-b", "feature-n", "main")
    make_commit(tmp_repo, "Feature N", "feature_n.txt")
    git(tmp_repo, "checkout", "main")
    make_merge_commit(tmp_repo, "feature-n", "Merge feature-n")

    cfg = Config(repo_path=str(tmp_repo), branches=["main"], hide_merges=False)
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    merge = next((cm for cm in main["commits"] if cm["title"] == "Merge feature-n"), None)
    assert merge is not None
    assert merge["hidden"] is False


# --- hide_if (header-based auto-hide) ---

def test_hide_if_header_shows_as_strip(tmp_repo):
    make_commit(tmp_repo, "Experimental commit", "exp.txt", body="Character: experimental")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 hide_if={"Character": ["experimental"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Experimental commit"), None)
    assert commit is not None
    assert commit["hidden"] is True


def test_hide_if_header_case_insensitive(tmp_repo):
    make_commit(tmp_repo, "Case test", "case.txt", body="CHARACTER: EXPERIMENTAL")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 hide_if={"character": ["experimental"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Case test"), None)
    assert commit is not None
    assert commit["hidden"] is True


def test_hide_if_non_matching_header_not_hidden(tmp_repo):
    make_commit(tmp_repo, "Normal commit", "norm.txt", body="Character: stable")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 hide_if={"Character": ["experimental"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Normal commit"), None)
    assert commit is not None
    assert commit["hidden"] is False


def test_unhide_auto_hidden_persists_via_shown_set(tmp_repo):
    make_commit(tmp_repo, "Auto-hide me", "auto.txt", body="Character: experimental")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 hide_if={"Character": ["experimental"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
        main = next(b for b in data["branches"] if b["name"] == "main")
        sha = next(cm["sha"] for cm in main["commits"] if cm["title"] == "Auto-hide me")

        # unhide via operation
        r = c.post("/api/operation", json={"type": "unhide", "sha": sha, "branch": "main"})
        assert r.json()["success"]

        # re-fetch state — should now be visible despite rule still active
        data2 = c.get("/api/state").json()
        main2 = next(b for b in data2["branches"] if b["name"] == "main")
        commit2 = next(cm for cm in main2["commits"] if cm["sha"] == sha)
        assert commit2["hidden"] is False

    # verify SHA is in shown set
    shown = _load_shown(cfg)
    assert sha in shown


# --- highlight_if ---

def test_highlight_if_sets_index(tmp_repo):
    make_commit(tmp_repo, "High priority", "hi.txt", body="Priority: high")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 highlight_if={"Priority": ["high", "critical"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "High priority"), None)
    assert commit is not None
    assert commit["highlight_index"] == 0


def test_highlight_if_first_rule_wins(tmp_repo):
    make_commit(tmp_repo, "Multi-rule", "multi.txt",
                body="Priority: high\nSeverity: critical")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 highlight_if={"Priority": ["high"], "Severity": ["critical"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Multi-rule"), None)
    assert commit["highlight_index"] == 0  # first rule wins


def test_highlight_if_no_match_is_null(tmp_repo):
    make_commit(tmp_repo, "Low priority", "lo.txt", body="Priority: low")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 highlight_if={"Priority": ["high"]})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Low priority"), None)
    assert commit["highlight_index"] is None


# --- issue_refs ---

def test_issue_refs_extracted(tmp_repo):
    make_commit(tmp_repo, "Fix two issues", "fix2.txt",
                body="Fixes #42\nSee also #7")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"])
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Fix two issues"), None)
    assert commit["issue_refs"] == ["42", "7"]


def test_issue_refs_deduped(tmp_repo):
    make_commit(tmp_repo, "Dup issue", "dup.txt", body="Fixes #42\nCloses #42")
    cfg = Config(repo_path=str(tmp_repo), branches=["main"])
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    commit = next((cm for cm in main["commits"] if cm["title"] == "Dup issue"), None)
    assert commit["issue_refs"] == ["42"]


def test_issue_refs_empty_when_none(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    for cm in main["commits"]:
        assert cm["issue_refs"] == []


# --- issue_url in state config ---

def test_issue_url_in_state_config(tmp_repo):
    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 issue_url="https://example.com/issues/")
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
    assert data["config"]["issue_url"] == "https://example.com/issues/"


def test_issue_url_null_when_not_configured(client):
    data = client.get("/api/state").json()
    assert data["config"]["issue_url"] is None


# --- pre_beginning ghost commits ---

def test_pre_beginning_commit_appears_in_other_branch(tmp_repo):
    # main: A → B → C → D → E;  stable/v1 beginning at B (so A is before beginning)
    # A (sha_a) should appear in stable/v1 column as pre_beginning=True
    sha_a = git(tmp_repo, "rev-parse", "HEAD~4").stdout.strip()  # "Initial commit"
    sha_b = git(tmp_repo, "rev-parse", "stable/v1").stdout.strip()

    cfg = Config(repo_path=str(tmp_repo), branches=["main", "stable/v1"],
                 branch_beginnings={"stable/v1": sha_b})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()

    v1 = next(b for b in data["branches"] if b["name"] == "stable/v1")
    # sha_a (Initial commit) should appear as pre_beginning in stable/v1
    ghost = next((cm for cm in v1["commits"] if cm["sha"] == sha_a), None)
    assert ghost is not None, "Initial commit (pre-beginning) should appear in stable/v1"
    assert ghost["pre_beginning"] is True


def test_pre_beginning_not_set_without_beginning_config(client):
    data = client.get("/api/state").json()
    for b in data["branches"]:
        for cm in b["commits"]:
            assert cm["pre_beginning"] is False


def test_pre_beginning_not_duplicated(tmp_repo):
    # stable/v1 already has [stable] Add B and [stable] Add C; they should not
    # also appear as pre_beginning ghosts since they're their own commits
    sha_b = git(tmp_repo, "rev-parse", "stable/v1~1").stdout.strip()
    cfg = Config(repo_path=str(tmp_repo), branches=["main", "stable/v1"],
                 branch_beginnings={"stable/v1": sha_b})
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()

    v1 = next(b for b in data["branches"] if b["name"] == "stable/v1")
    # Each SHA should appear at most once in stable/v1's commits
    v1_shas = [cm["sha"] for cm in v1["commits"]]
    assert len(v1_shas) == len(set(v1_shas)), "No SHA should appear twice in the same branch"


# --- amend operation ---

def test_amend_operation_changes_message(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha = next(c["sha"] for c in main["commits"] if c["title"] == "Add E")

    r = client.post("/api/operation", json={
        "type": "amend",
        "amendments": [{"sha": sha, "branch": "main"}],
        "message": "Add E (amended)",
    })
    assert r.json()["success"]

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    titles = [c["title"] for c in main2["commits"]]
    assert "Add E (amended)" in titles
    assert "Add E" not in titles


def test_amend_operation_changes_author(client):
    data = client.get("/api/state").json()
    main = next(b for b in data["branches"] if b["name"] == "main")
    sha = next(c["sha"] for c in main["commits"] if c["title"] == "Add E")

    r = client.post("/api/operation", json={
        "type": "amend",
        "amendments": [{"sha": sha, "branch": "main"}],
        "author": "Alice <alice@example.com>",
    })
    assert r.json()["success"]

    data2 = client.get("/api/state").json()
    main2 = next(b for b in data2["branches"] if b["name"] == "main")
    tip = next(c for c in main2["commits"] if c["title"] == "Add E")
    assert "Alice" in tip["author"]


def test_amend_operation_multi_branch(tmp_repo):
    # Two branches each have a commit; amend both in one operation.
    cfg = Config(repo_path=str(tmp_repo), branches=["main", "stable/v1"])
    app = create_app(cfg)
    with TestClient(app) as c:
        data = c.get("/api/state").json()
        main = next(b for b in data["branches"] if b["name"] == "main")
        v1 = next(b for b in data["branches"] if b["name"] == "stable/v1")
        sha_main = next(cm["sha"] for cm in main["commits"] if cm["title"] == "Add E")
        sha_v1 = next(cm["sha"] for cm in v1["commits"] if cm["title"] == "[stable] Add C")

        r = c.post("/api/operation", json={
            "type": "amend",
            "amendments": [
                {"sha": sha_main, "branch": "main"},
                {"sha": sha_v1, "branch": "stable/v1"},
            ],
            "message": "Amended on both",
        })
        assert r.json()["success"]

        data2 = c.get("/api/state").json()
        main2 = next(b for b in data2["branches"] if b["name"] == "main")
        v1_2 = next(b for b in data2["branches"] if b["name"] == "stable/v1")
        assert any(cm["title"] == "Amended on both" for cm in main2["commits"])
        assert any(cm["title"] == "Amended on both" for cm in v1_2["commits"])


# --- autosquash operation ---

def test_autosquash_operation_squashes_fixup(tmp_repo):
    git(tmp_repo, "checkout", "-b", "fix-branch", "main")
    make_commit(tmp_repo, "Add feature", "feat_op.txt")
    sha_fixup = make_commit(tmp_repo, "fixup! Add feature", "feat_fix_op.txt")

    cfg = Config(repo_path=str(tmp_repo), branches=["fix-branch"])
    app = create_app(cfg)
    with TestClient(app) as c:
        r = c.post("/api/operation", json={
            "type": "autosquash",
            "branch": "fix-branch",
            "sha": sha_fixup,
        })
        assert r.json()["success"], r.json().get("error")

        data = c.get("/api/state").json()
        branch = next(b for b in data["branches"] if b["name"] == "fix-branch")
        titles = [cm["title"] for cm in branch["commits"]]
        assert "Add feature" in titles
        assert not any("fixup!" in t for t in titles)


# --- relevant_remotes ---

def test_relevant_remotes_shows_remote_ref(tmp_path, tmp_repo):
    # Create a bare clone to use as a remote
    bare = tmp_path / "origin.git"
    git(tmp_repo, "clone", "--bare", str(tmp_repo), str(bare))
    git(tmp_repo, "remote", "add", "origin", str(bare))
    git(tmp_repo, "fetch", "origin")

    cfg = Config(repo_path=str(tmp_repo), branches=["main"],
                 relevant_remotes=["origin"])
    app = create_app(cfg)
    try:
        with TestClient(app) as c:
            data = c.get("/api/state").json()
        main = next(b for b in data["branches"] if b["name"] == "main")
        tip = main["commits"][0]
        ref_types = [r["type"] for r in tip["refs"]]
        assert "remote" in ref_types
        ref_names = [r["name"] for r in tip["refs"]]
        assert any("origin/main" in n for n in ref_names)
    finally:
        git(tmp_repo, "remote", "remove", "origin")
