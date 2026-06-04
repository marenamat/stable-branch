import atexit
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OpResult:
    success: bool
    error: str = ""
    command: str = ""


class GitWorktree:
    def __init__(self, repo_path: str):
        self.repo = Path(repo_path).resolve()
        self.wt = Path(f"/tmp/stable-branch-{os.getpid()}")
        self._create_wt()
        atexit.register(self.cleanup)

    def _git(self, *args, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.wt,
            capture_output=True,
            text=True,
        )

    def _create_wt(self):
        if self.wt.exists():
            return
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(self.wt)],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Cannot create worktree: {r.stderr.strip()}")

    def cleanup(self):
        if self.wt.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.wt)],
                cwd=self.repo,
                capture_output=True,
            )

    # --- read operations ---

    def get_commits(self, branch: str, since: str | None = None) -> list[dict]:
        rev_range = f"{since}..{branch}" if since else branch
        # \x1e (ASCII 30, Record Separator) between commits; \x00 between fields.
        # %B is the full message (subject + body); %P is space-separated parent SHAs.
        r = self._git(
            "log", "--format=%H%x00%s%x00%aN%x00%at%x00%P%x00%B%x1e", rev_range,
            cwd=self.repo,
        )
        commits = []
        for record in r.stdout.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            parts = record.split("\x00", 5)
            if len(parts) != 6:
                continue
            sha, title, author, ts, parents, body = parts
            commits.append({
                "sha": sha,
                "short_sha": sha[:8],
                "title": title,
                "author": author,
                "timestamp": int(ts),
                "is_merge": len(parents.split()) > 1,
                "body": body.strip(),
            })
        return commits

    def refs_by_sha(self, sha_set: set[str]) -> dict[str, list[dict]]:
        """Return {sha: [{"name": ..., "type": "branch"|"tag"}, ...]} for all refs in sha_set."""
        result: dict[str, list[dict]] = {}

        r = self._git(
            "for-each-ref", "refs/heads",
            "--format=%(objectname)%09%(refname:short)",
            cwd=self.repo,
        )
        for line in r.stdout.splitlines():
            sha, _, name = line.partition("\t")
            if sha in sha_set:
                result.setdefault(sha, []).append({"name": name, "type": "branch"})

        # %(*objectname) is the dereferenced commit SHA for annotated tags; empty for lightweight
        r = self._git(
            "for-each-ref", "refs/tags",
            "--format=%(objectname)%09%(*objectname)%09%(refname:short)",
            cwd=self.repo,
        )
        for line in r.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            obj_sha, deref_sha, name = parts
            commit_sha = deref_sha if deref_sha else obj_sha
            if commit_sha in sha_set:
                result.setdefault(commit_sha, []).append({"name": name, "type": "tag"})

        return result

    def commit_message(self, sha: str) -> str:
        r = self._git("log", "-1", "--format=%B", sha, cwd=self.repo)
        return r.stdout.strip()

    def commit_diff(self, sha: str) -> str:
        r = self._git("diff-tree", "--no-commit-id", "-p", "-r", sha, cwd=self.repo)
        return r.stdout

    def range_diff(self, sha1: str, sha2: str) -> str:
        r = self._git(
            "range-diff", f"{sha1}^..{sha1}", f"{sha2}^..{sha2}",
            cwd=self.repo,
        )
        return r.stdout or r.stderr

    # --- write operations (run in worktree) ---

    def _tmp_branch(self) -> str:
        return f"_sb_{os.getpid()}"

    def _checkout_tmp(self, branch: str) -> OpResult | None:
        tmp = self._tmp_branch()
        r = self._git("checkout", "-b", tmp, branch)
        if r.returncode != 0:
            return OpResult(False, r.stderr, f"git checkout -b {tmp} {branch}")
        return None

    def _commit_tmp_back(self, branch: str) -> None:
        new_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("update-ref", f"refs/heads/{branch}", new_sha, cwd=self.repo)

    def _drop_tmp(self):
        tmp = self._tmp_branch()
        self._git("checkout", "--detach", "HEAD")
        self._git("branch", "-D", tmp)

    def cherry_pick(self, commit_sha: str, target_branch: str) -> OpResult:
        err = self._checkout_tmp(target_branch)
        if err:
            return err

        cmd = f"git cherry-pick {commit_sha}"
        r = self._git("cherry-pick", commit_sha)
        if r.returncode != 0:
            self._git("cherry-pick", "--abort")
            self._drop_tmp()
            return OpResult(False, r.stdout + r.stderr, cmd)

        self._commit_tmp_back(target_branch)
        self._drop_tmp()
        return OpResult(True)

    def reorder(self, branch: str, new_order: list[str]) -> OpResult:
        """new_order: commit SHAs newest-first (desired display order after reorder)."""
        # Identify root commits (no parent) — they cannot be rebased and serve as the base.
        root_shas: set[str] = set()
        for sha in new_order:
            r = self._git("rev-parse", f"{sha}^", cwd=self.repo)
            if r.returncode != 0:
                root_shas.add(sha)

        rebasable_shas = [s for s in new_order if s not in root_shas]
        if not rebasable_shas:
            return OpResult(False, "Cannot reorder: only root commits present", "")

        rebasable_set = set(rebasable_shas)
        base: str | None = None
        for sha in rebasable_shas:
            r = self._git("rev-parse", f"{sha}^", cwd=self.repo)
            if r.returncode == 0:
                parent = r.stdout.strip()
                if parent not in rebasable_set:
                    base = parent
                    break
        if base is None:
            return OpResult(False, "Cannot determine rebase base", "")

        err = self._checkout_tmp(branch)
        if err:
            return err

        # Build rebase todo oldest-first (exclude root commits — they stay in place)
        todo = "\n".join(f"pick {sha}" for sha in reversed(rebasable_shas)) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".todo", delete=False, dir="/tmp"
        ) as tf:
            tf.write(todo)
            todo_file = tf.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, dir="/tmp"
        ) as sf:
            sf.write("#!/bin/sh\n")
            sf.write(f"cp {todo_file} \"$1\"\n")
            script = sf.name
        os.chmod(script, 0o755)

        cmd = f"GIT_SEQUENCE_EDITOR={script} git rebase -i {base}"
        env = {**os.environ, "GIT_SEQUENCE_EDITOR": script}
        r2 = subprocess.run(
            ["git", "rebase", "-i", base],
            cwd=self.wt, env=env, capture_output=True, text=True,
        )
        for f in (script, todo_file):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

        if r2.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=self.wt, capture_output=True)
            self._drop_tmp()
            return OpResult(False, r2.stdout + r2.stderr, cmd)

        self._commit_tmp_back(branch)
        self._drop_tmp()
        return OpResult(True)

    def delete_commit(self, branch: str, commit_sha: str) -> OpResult:
        err = self._checkout_tmp(branch)
        if err:
            return err

        cmd = f"git rebase --onto {commit_sha}^ {commit_sha}"
        r = self._git("rebase", "--onto", f"{commit_sha}^", commit_sha)
        if r.returncode != 0:
            self._git("rebase", "--abort")
            self._drop_tmp()
            return OpResult(False, r.stdout + r.stderr, cmd)

        self._commit_tmp_back(branch)
        self._drop_tmp()
        return OpResult(True)
