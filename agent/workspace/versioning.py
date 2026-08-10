import json
import re
from pathlib import Path

import httpx

from agent.domain.errors import SourceRevisionError


_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def read_extension_version(manifest_path: Path) -> str:
    """读取可选构建元数据；缺失或损坏不能阻断后端修复。"""

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        return "unknown"
    return version.strip()


class GitHubMainRevisionReader:
    """通过已认证 Client 读取并校验任务固定使用的 GitHub main SHA。"""

    def __init__(
        self,
        repository: str,
        *,
        client: httpx.AsyncClient,
        api_url: str = "https://api.github.com",
    ) -> None:
        if not _GITHUB_REPOSITORY.fullmatch(repository):
            raise SourceRevisionError("GITHUB_REPOSITORY must use owner/name format")
        self._repository = repository
        self._client = client
        self._api_url = api_url.rstrip("/")

    async def read_main_sha(self) -> str:
        try:
            response = await self._client.get(
                f"{self._api_url}/repos/{self._repository}/commits/main",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.HTTPError as exc:
            raise SourceRevisionError(
                f"GitHub revision request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise SourceRevisionError(
                f"GitHub revision request failed with status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceRevisionError("GitHub returned invalid revision JSON") from exc
        sha = payload.get("sha") if isinstance(payload, dict) else None
        # 只接受完整 commit SHA，拒绝分支名、短 SHA 和响应正文中的任意字符串。
        if not isinstance(sha, str) or not _COMMIT_SHA.fullmatch(sha):
            raise SourceRevisionError("GitHub returned an invalid main commit SHA")
        return sha
