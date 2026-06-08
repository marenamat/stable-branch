import atexit
import os
import shlex
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
            encoding='utf-8',
            errors='replace',
        )

    def _create_wt(self):
        if self.wt.exists():
            return
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(self.wt)],
            cwd=self.repo,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
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
            "log", "--format=%H%x00%s%x00%aN <%aE>%x00%at%x00%ct%x00%P%x00%B%x1e", rev_range,
            cwd=self.repo,
        )
        commits = []
        for record in r.stdout.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            parts = record.split("\x00", 6)
            if len(parts) != 7:
                continue
            sha, title, author, ts, cts, parents, body = parts
            commits.append({
                "sha": sha,
                "short_sha": sha[:8],
                "title": title,
                "author": author,
                "timestamp": int(ts),
                "committer_timestamp": int(cts),
                "is_merge": len(parents.split()) > 1,
                "body": body.strip(),
            })
        return commits

    def refs_by_sha(
        self,
        sha_set: set[str],
        relevant_remotes: list[str] | None = None,
    ) -> dict[str, list[dict]]:
        """Return {sha: [{"name": ..., "type": "branch"|"tag"|"remote"}, ...]}."""
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

        if relevant_remotes:
            for remote in relevant_remotes:
                r = self._git(
                    "for-each-ref", f"refs/remotes/{remote}",
                    "--format=%(objectname)%09%(refname:short)",
                    cwd=self.repo,
                )
                for line in r.stdout.splitlines():
                    sha, _, name = line.partition("\t")
                    if sha in sha_set and not name.endswith("/HEAD"):
                        result.setdefault(sha, []).append({"name": name, "type": "remote"})

        return result

    def ancestry_shas(self, ref: str) -> set[str]:
        """Return the set of all commit SHAs reachable from ref (inclusive)."""
        r = self._git("rev-list", ref, cwd=self.repo)
        return set(r.stdout.split())

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

    def _validate_no_merge_crossing(self, new_order: list[str], branch: str) -> str | None:
        """Return error message if new_order moves any commit across a merge boundary."""
        if len(new_order) <= 1:
            return None
        r = self._git("log", "--format=%H %P", branch, cwd=self.repo)
        sha_parents: dict[str, list[str]] = {}
        current_all: list[str] = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            sha_parents[parts[0]] = parts[1:]
            current_all.append(parts[0])
        new_order_set = set(new_order)
        current_filtered = [s for s in current_all if s in new_order_set]
        for sha in new_order:
            if len(sha_parents.get(sha, [])) <= 1:
                continue
            cur_idx = current_filtered.index(sha)
            new_idx = new_order.index(sha)
            if set(current_filtered[:cur_idx]) != set(new_order[:new_idx]):
                return f"Cannot reorder across merge commit {sha[:8]}"
        return None

    def reorder(self, branch: str, new_order: list[str]) -> OpResult:
        """new_order: commit SHAs newest-first (desired display order after reorder)."""
        err_msg = self._validate_no_merge_crossing(new_order, branch)
        if err_msg:
            return OpResult(False, err_msg, "")

        # Classify each SHA as root, merge, or regular
        root_shas: set[str] = set()
        merge_shas: set[str] = set()
        for sha in new_order:
            r = self._git("log", "-1", "--format=%P", sha, cwd=self.repo)
            if r.returncode == 0:
                parents = r.stdout.strip().split()
                if not parents:
                    root_shas.add(sha)
                elif len(parents) > 1:
                    merge_shas.add(sha)

        # Top segment: non-merge, non-root commits from the tip down to the first merge/root.
        # Only the top segment can be reordered — commits below a merge cannot be moved
        # without corrupting the merge's parent chain.
        top_segment: list[str] = []
        for sha in new_order:
            if sha in merge_shas or sha in root_shas:
                break
            top_segment.append(sha)

        # Verify that no positions changed outside the top segment
        new_order_set = set(new_order)
        r = self._git("log", "--format=%H", branch, cwd=self.repo)
        current_filtered = [s for s in r.stdout.split() if s in new_order_set]
        top_segment_set = set(top_segment)
        if ([s for s in current_filtered if s not in top_segment_set] !=
                [s for s in new_order if s not in top_segment_set]):
            return OpResult(False, "Reordering below a merge commit is not supported", "")

        rebasable_shas = top_segment
        if not rebasable_shas:
            return OpResult(True)

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
            cwd=self.wt, env=env, capture_output=True, encoding='utf-8', errors='replace',
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

    def amend_commit(
        self,
        branch: str,
        sha: str,
        new_message: str | None,
        new_author: str | None,
    ) -> OpResult:
        """Amend the message and/or author of an existing commit.

        Fails if the commit is a root commit or if there are merge commits
        between sha and the branch tip (rebasing across merges would corrupt
        the merge's parent chain).
        """
        r = self._git("rev-parse", f"{sha}^", cwd=self.repo)
        if r.returncode != 0:
            return OpResult(False, "Cannot amend root commit", f"git rev-parse {sha}^")
        base = r.stdout.strip()

        err = self._checkout_tmp(branch)
        if err:
            return err

        # Collect commits from base to branch tip (newest-first); detect merges.
        r = self._git("log", "--format=%H %P", f"{base}..{branch}", cwd=self.repo)
        commits_newest_first: list[str] = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            if len(parts) > 2:  # sha + 2+ parents = merge commit
                self._drop_tmp()
                return OpResult(
                    False,
                    "Cannot amend a commit with merge commits above it",
                    "",
                )
            commits_newest_first.append(parts[0])

        if not commits_newest_first:
            self._drop_tmp()
            return OpResult(False, "Commit not found in branch range", "")

        msg_file: str | None = None
        if new_message is not None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".msg", delete=False, dir="/tmp"
            ) as mf:
                mf.write(new_message)
                msg_file = mf.name

        # Build rebase todo (oldest-first).
        # For the target sha, inject an exec right after pick to amend it.
        todo_lines: list[str] = []
        for c_sha in reversed(commits_newest_first):
            todo_lines.append(f"pick {c_sha}")
            if c_sha == sha:
                amend_parts = ["git", "commit", "--amend"]
                if msg_file:
                    amend_parts += ["-F", msg_file]
                else:
                    amend_parts.append("--no-edit")
                if new_author is not None:
                    amend_parts += ["--author", new_author]
                todo_lines.append("exec " + " ".join(shlex.quote(p) for p in amend_parts))
        todo = "\n".join(todo_lines) + "\n"

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
            seq_script = sf.name
        os.chmod(seq_script, 0o755)

        cmd = f"GIT_SEQUENCE_EDITOR={seq_script} git rebase -i {base}"
        env = {**os.environ, "GIT_SEQUENCE_EDITOR": seq_script}
        r2 = subprocess.run(
            ["git", "rebase", "-i", base],
            cwd=self.wt, env=env, capture_output=True, encoding='utf-8', errors='replace',
        )

        for f in [seq_script, todo_file] + ([msg_file] if msg_file else []):
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

    def autosquash(self, branch: str, sha: str) -> OpResult:
        """Squash a fixup!/squash! commit into its target via git rebase --autosquash.

        Strips all fixup!/squash! prefixes from the commit title to find the root
        original commit, then rebases the whole range so git can collapse every
        fixup/squash variant of that original into it in one pass.
        """
        r = self._git("log", "-1", "--format=%s", sha, cwd=self.repo)
        if r.returncode != 0:
            return OpResult(False, f"Cannot read commit {sha[:8]}", "")
        title = r.stdout.strip()

        # Strip all fixup!/squash! layers to reach the root target title.
        base_title = title
        while base_title.startswith("fixup! ") or base_title.startswith("squash! "):
            base_title = base_title.split(" ", 1)[1]

        # Find the most recent commit in the branch with that root title.
        r = self._git("log", "--format=%H%x00%s", branch, cwd=self.repo)
        original_sha = None
        for line in r.stdout.splitlines():
            if not line:
                continue
            c_sha, _, c_title = line.partition("\x00")
            if c_title == base_title and c_sha != sha:
                original_sha = c_sha
                break  # git log is newest-first; take the most recent match

        if original_sha is None:
            return OpResult(False, f"No commit with title {base_title!r} found in {branch}", "")

        r = self._git("rev-parse", f"{original_sha}^", cwd=self.repo)
        if r.returncode != 0:
            return OpResult(False, "Original commit has no parent (root commit)", f"git rev-parse {original_sha[:8]}^")
        base = r.stdout.strip()

        # Reject if any merge commits exist in the rebase range.
        r = self._git("log", "--format=%P", f"{base}..{branch}", cwd=self.repo)
        for line in r.stdout.splitlines():
            if len(line.split()) > 1:
                return OpResult(False, "Cannot autosquash: merge commits in range", "")

        err = self._checkout_tmp(branch)
        if err:
            return err

        cmd = f"GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash {base}"
        env = {**os.environ, "GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}
        r2 = subprocess.run(
            ["git", "rebase", "-i", "--autosquash", base],
            cwd=self.wt, env=env, capture_output=True, encoding='utf-8', errors='replace',
        )

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
