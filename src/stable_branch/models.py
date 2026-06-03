from dataclasses import dataclass, field


@dataclass
class Commit:
    sha: str
    short_sha: str
    title: str
    author: str
    timestamp: int
    branch: str
    group_id: str | None = None
    color_index: int | None = None
    hidden: bool = False


@dataclass
class Branch:
    name: str
    commits: list[Commit] = field(default_factory=list)


@dataclass
class Group:
    id: str
    color_index: int
    commit_shas: list[str] = field(default_factory=list)


@dataclass
class Config:
    repo_path: str
    branches: list[str]
    port: int = 8000
    match_threshold: float = 0.80
    match_by_author: bool = False
    branch_beginnings: dict[str, str] = field(default_factory=dict)
    flush_hidden: bool = False
    open_browser: bool = False
