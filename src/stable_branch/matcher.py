import difflib
import hashlib
import re

from .models import Branch, Group

_STRIP = [
    re.compile(r"^\[stable[^\]]*\]\s*", re.IGNORECASE),
    re.compile(r"^BACKPORT\s+", re.IGNORECASE),
    re.compile(r"^backport:\s*", re.IGNORECASE),
    re.compile(r"\s*\(cherry picked from commit [0-9a-f]+\)\s*$", re.IGNORECASE),
    re.compile(r"\s*\(cherry-pick of [0-9a-f]+\)\s*$", re.IGNORECASE),
]


def _normalize(title: str) -> str:
    t = title.strip()
    for pat in _STRIP:
        t = pat.sub("", t).strip()
    return t


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def assign_groups(
    branches: list[Branch],
    threshold: float = 0.80,
    by_author: bool = False,
) -> list[Group]:
    sha_to_group: dict[str, str] = {}
    group_shas: dict[str, set[str]] = {}

    branch_names = [b.name for b in branches]
    commits_by_branch = {b.name: b.commits for b in branches}

    for i, b1_name in enumerate(branch_names):
        for b2_name in branch_names[i + 1 :]:
            c1_list = commits_by_branch[b1_name]
            c2_list = commits_by_branch[b2_name]
            matched_c2: set[str] = set()

            for c1 in c1_list:
                best_ratio = threshold
                best_sha2 = None

                for c2 in c2_list:
                    if c2.sha in matched_c2:
                        continue
                    if by_author and c1.author != c2.author:
                        continue
                    r = _similarity(c1.title, c2.title)
                    if r >= best_ratio:
                        best_ratio = r
                        best_sha2 = c2.sha

                if best_sha2 is None:
                    continue

                matched_c2.add(best_sha2)
                g1 = sha_to_group.get(c1.sha)
                g2 = sha_to_group.get(best_sha2)

                if g1 is None and g2 is None:
                    gid = hashlib.sha256(f"{c1.sha}:{best_sha2}".encode()).hexdigest()[:16]
                    group_shas[gid] = {c1.sha, best_sha2}
                    sha_to_group[c1.sha] = gid
                    sha_to_group[best_sha2] = gid
                elif g1 is not None and g2 is None:
                    group_shas[g1].add(best_sha2)
                    sha_to_group[best_sha2] = g1
                elif g1 is None and g2 is not None:
                    group_shas[g2].add(c1.sha)
                    sha_to_group[c1.sha] = g2
                elif g1 != g2:
                    # merge g2 into g1
                    group_shas[g1].update(group_shas.pop(g2))
                    for sha in list(sha_to_group):
                        if sha_to_group[sha] == g2:
                            sha_to_group[sha] = g1

    groups: list[Group] = []
    for gid, shas in group_shas.items():
        color_index = int(gid[:4], 16) % 16
        g = Group(id=gid, color_index=color_index, commit_shas=list(shas))
        groups.append(g)

    for g in groups:
        for sha in g.commit_shas:
            for b in branches:
                for c in b.commits:
                    if c.sha == sha:
                        c.group_id = g.id
                        c.color_index = g.color_index

    return groups
