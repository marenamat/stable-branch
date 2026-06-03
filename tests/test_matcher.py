import pytest
from stable_branch.matcher import assign_groups, _normalize
from stable_branch.models import Branch, Commit


def _commit(sha: str, title: str, branch: str = "b") -> Commit:
    return Commit(sha=sha, short_sha=sha[:8], title=title, author="a", timestamp=0, branch=branch)


def _branch(name: str, commits: list[Commit]) -> Branch:
    return Branch(name=name, commits=commits)


# --- normalization ---

@pytest.mark.parametrize("raw,expected", [
    ("[stable] Fix bug",       "Fix bug"),
    ("[stable/v1] Fix bug",    "Fix bug"),
    ("BACKPORT Fix bug",       "Fix bug"),
    ("backport: Fix bug",      "Fix bug"),
    ("Fix bug (cherry picked from commit abc1234)", "Fix bug"),
    ("Fix bug\n",              "Fix bug"),
    ("Fix bug",                "Fix bug"),
])
def test_normalize(raw, expected):
    assert _normalize(raw) == expected


# --- basic matching ---

def test_exact_title_match():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "Fix crash", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert len(groups) == 1
    assert set(groups[0].commit_shas) == {"aaa", "bbb"}


def test_backport_prefix_match():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "BACKPORT Fix crash", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert len(groups) == 1


def test_stable_prefix_match():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "[stable] Fix crash", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert len(groups) == 1


def test_no_match_different_titles():
    c1 = _commit("aaa", "Fix login bug", "main")
    c2 = _commit("bbb", "Add dark mode", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert len(groups) == 0


def test_threshold_respected():
    c1 = _commit("aaa", "Fix crash in module", "main")
    c2 = _commit("bbb", "Fix crash in module", "stable/v1")
    groups_high = assign_groups(
        [_branch("main", [c1]), _branch("stable/v1", [c2])], threshold=1.0
    )
    groups_low = assign_groups(
        [_branch("main", [c1]), _branch("stable/v1", [c2])], threshold=0.5
    )
    assert len(groups_high) == 1
    assert len(groups_low) == 1


def test_three_branch_group():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "[stable] Fix crash", "stable/v1")
    c3 = _commit("ccc", "BACKPORT Fix crash", "stable/v2")
    groups = assign_groups([
        _branch("main", [c1]),
        _branch("stable/v1", [c2]),
        _branch("stable/v2", [c3]),
    ])
    assert len(groups) == 1
    assert set(groups[0].commit_shas) == {"aaa", "bbb", "ccc"}


def test_no_cross_branch_same_branch():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "Fix crash", "main")
    groups = assign_groups([_branch("main", [c1, c2])])
    assert len(groups) == 0


def test_color_index_assigned():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "Fix crash", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert 0 <= groups[0].color_index <= 15


def test_commits_get_group_id():
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "Fix crash", "stable/v1")
    branches = [_branch("main", [c1]), _branch("stable/v1", [c2])]
    assign_groups(branches)
    assert c1.group_id is not None
    assert c1.group_id == c2.group_id
    assert c1.color_index == c2.color_index


def test_by_author_same():
    c1 = _commit("aaa", "Fix crash", "main")
    c1.author = "alice"
    c2 = _commit("bbb", "Fix crash", "stable/v1")
    c2.author = "alice"
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])], by_author=True)
    assert len(groups) == 1


def test_by_author_different():
    c1 = _commit("aaa", "Fix crash", "main")
    c1.author = "alice"
    c2 = _commit("bbb", "Fix crash", "stable/v1")
    c2.author = "bob"
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])], by_author=True)
    assert len(groups) == 0


# --- same SHA (shared base commit) ---

def test_same_sha_not_grouped():
    """Commits with identical SHAs (shared base) must not be grouped as backport pairs."""
    c1 = _commit("base", "Initial commit", "main")
    c2 = _commit("base", "Initial commit", "stable/v1")
    groups = assign_groups([_branch("main", [c1]), _branch("stable/v1", [c2])])
    assert len(groups) == 0


def test_same_sha_not_grouped_with_other_matches():
    """Shared base commits don't produce spurious groups; real backports still match."""
    c_base_main = _commit("base", "Initial commit", "main")
    c_base_v1 = _commit("base", "Initial commit", "stable/v1")
    c1 = _commit("aaa", "Fix crash", "main")
    c2 = _commit("bbb", "[stable] Fix crash", "stable/v1")
    groups = assign_groups([
        _branch("main", [c1, c_base_main]),
        _branch("stable/v1", [c2, c_base_v1]),
    ])
    assert len(groups) == 1
    assert set(groups[0].commit_shas) == {"aaa", "bbb"}
