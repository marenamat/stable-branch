import os
import subprocess
from pathlib import Path

import pytest


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(repo: Path, *args, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed:\n{r.stderr}")
    return r


def make_commit(repo: Path, message: str, filename: str | None = None, body: str = "") -> str:
    fname = filename or (message.replace(" ", "_").lower()[:20] + ".txt")
    (repo / fname).write_text(message + "\n")
    git(repo, "add", fname)
    full_message = f"{message}\n\n{body}" if body else message
    git(repo, "commit", "-m", full_message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def make_merge_commit(repo: Path, branch: str, message: str) -> str:
    git(repo, "merge", "--no-ff", "-m", message, branch)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def tmp_repo(tmp_path):
    """
    Repo layout:
      main:      A → B → C → D → E   (newest = E, A = initial)
      stable/v1: A → Bs → Cs         ("[stable] Add B", "[stable] Add C")
      stable/v2: A → Bb              ("BACKPORT Add B")

    A is the shared base commit.
    B/Bs/Bb are the same logical change with different title prefixes → matcher should group them.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test Author")

    sha_a = make_commit(repo, "Initial commit", "base.txt")
    sha_b = make_commit(repo, "Add B", "b.txt")
    sha_c = make_commit(repo, "Add C", "c.txt")
    make_commit(repo, "Add D", "d.txt")
    make_commit(repo, "Add E", "e.txt")

    # stable/v1: start from A, add [stable] variants of B and C
    git(repo, "checkout", "-b", "stable/v1", sha_a)
    make_commit(repo, "[stable] Add B", "b.txt")
    make_commit(repo, "[stable] Add C", "c.txt")

    # stable/v2: start from A, add BACKPORT variant of B
    git(repo, "checkout", "-b", "stable/v2", sha_a)
    make_commit(repo, "BACKPORT Add B", "b.txt")

    git(repo, "checkout", "main")

    return repo
