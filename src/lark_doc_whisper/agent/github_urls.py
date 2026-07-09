"""GitHub URL parsing helpers shared by URL fetch and MCP policy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..security.policy import AllowedUrl


_REPO_QUALIFIER_RE = re.compile(r"(?i)\brepo:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str

    @property
    def key(self) -> str:
        return f"{self.owner.lower()}/{self.repo.lower()}"


def parse_github_repo_url(url: str) -> GitHubRepoRef | None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host == "github.com" and len(parts) >= 2:
        repo = parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return GitHubRepoRef(owner=parts[0], repo=repo)
    if host == "raw.githubusercontent.com" and len(parts) >= 2:
        return GitHubRepoRef(owner=parts[0], repo=parts[1])
    return None


def is_github_url(url: str) -> bool:
    return parse_github_repo_url(url) is not None


def allowed_github_repo_keys(allowed_urls: tuple[AllowedUrl, ...]) -> set[str]:
    keys: set[str] = set()
    for item in allowed_urls:
        repo = parse_github_repo_url(item.url)
        if repo is not None:
            keys.add(repo.key)
    return keys


def github_repo_key_from_mcp_args(args: dict[str, object]) -> str:
    owner = args.get("owner")
    repo = args.get("repo")
    if isinstance(owner, str) and isinstance(repo, str) and owner and repo:
        return GitHubRepoRef(owner=owner, repo=repo.removesuffix(".git")).key

    for value in args.values():
        if isinstance(value, str):
            url_repo = parse_github_repo_url(value)
            if url_repo is not None:
                return url_repo.key
            qualifier = _REPO_QUALIFIER_RE.search(value)
            if qualifier:
                return GitHubRepoRef(owner=qualifier.group(1), repo=qualifier.group(2)).key
    return ""
