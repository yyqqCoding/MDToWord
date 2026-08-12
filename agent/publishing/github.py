"""使用 GitHub App 最小权限令牌发布固定分支、提交和 Pull Request。"""

import asyncio
import base64
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
import jwt

from agent.domain.errors import (
    PublicationAuthenticationError,
    PublicationConflictError,
    PublicationError,
)
from agent.publishing.contracts import (
    PublicationDisposition,
    PublicationRequest,
    PublicationResult,
    PullRequestPublisher,
)
from agent.telemetry.masking import mask_text


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class InstallationTokenProvider(Protocol):
    async def get_token(self) -> str: ...


class GitHubAppTokenProvider:
    """用 App 私钥按需换取短期安装令牌；令牌只保存在进程内。"""

    def __init__(
        self,
        repository: str,
        *,
        app_id: str,
        private_key: str,
        client: httpx.AsyncClient,
        api_url: str = "https://api.github.com",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise PublicationAuthenticationError(
                "GITHUB_REPOSITORY must use owner/name format"
            )
        if not app_id.strip() or "PRIVATE KEY" not in private_key:
            raise PublicationAuthenticationError("GitHub App credentials are invalid")
        self._repository = repository
        self._app_id = app_id.strip()
        self._private_key = private_key
        self._client = client
        self._api_url = api_url.rstrip("/")
        self._now = now or (lambda: datetime.now(UTC))
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            now = self._now()
            if (
                self._cached_token is not None
                and self._expires_at is not None
                and now < self._expires_at - timedelta(minutes=1)
            ):
                return self._cached_token

            app_jwt = self._build_app_jwt(now)
            installation = await self._request_json(
                "GET",
                f"/repos/{self._repository}/installation",
                headers={"Authorization": f"Bearer {app_jwt}"},
            )
            installation_id = installation.get("id")
            if not isinstance(installation_id, int) or installation_id < 1:
                raise PublicationAuthenticationError(
                    "GitHub App installation response is invalid"
                )
            token_payload = await self._request_json(
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}"},
                json={
                    "repositories": [self._repository.split("/", 1)[1]],
                    "permissions": {
                        "contents": "write",
                        "pull_requests": "write",
                    },
                },
            )
            token = token_payload.get("token")
            expires_at = token_payload.get("expires_at")
            if not isinstance(token, str) or not token or not isinstance(expires_at, str):
                raise PublicationAuthenticationError(
                    "GitHub App token response is invalid"
                )
            permissions = token_payload.get("permissions")
            allowed_permissions = {
                "contents": "write",
                "pull_requests": "write",
                # GitHub App 固有的 metadata:read 可能出现在令牌响应中。
                "metadata": "read",
            }
            if (
                not isinstance(permissions, dict)
                or permissions.get("contents") != "write"
                or permissions.get("pull_requests") != "write"
                or any(
                    allowed_permissions.get(str(name)) != level
                    for name, level in permissions.items()
                )
            ):
                raise PublicationAuthenticationError(
                    "GitHub App token permissions are not minimal"
                )
            try:
                parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise PublicationAuthenticationError(
                    "GitHub App token expiry is invalid"
                ) from exc
            if parsed_expiry.tzinfo is None or parsed_expiry <= now:
                raise PublicationAuthenticationError(
                    "GitHub App token expiry is invalid"
                )
            self._cached_token = token
            self._expires_at = parsed_expiry
            return token

    def _build_app_jwt(self, now: datetime) -> str:
        try:
            encoded = jwt.encode(
                {
                    "iat": int((now - timedelta(seconds=60)).timestamp()),
                    "exp": int((now + timedelta(minutes=9)).timestamp()),
                    "iss": self._app_id,
                },
                self._private_key,
                algorithm="RS256",
            )
        except Exception as exc:
            # cryptography 的原始异常可能回显私钥解析细节，只暴露稳定错误。
            raise PublicationAuthenticationError(
                "GitHub App JWT signing failed"
            ) from exc
        return encoded

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.request(
                method,
                f"{self._api_url}{path}",
                headers={**_API_HEADERS, **headers},
                json=json,
            )
        except httpx.HTTPError as exc:
            raise PublicationAuthenticationError(
                f"GitHub App authentication failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise PublicationAuthenticationError(
                f"GitHub App authentication failed with status {response.status_code}"
            )
        return _json_object(response, authentication=True)


class GitHubPullRequestPublisher(PullRequestPublisher):
    """通过 Git Data API 一次提交所有受控文件，并且不提供合并入口。"""

    def __init__(
        self,
        repository: str,
        *,
        token_provider: InstallationTokenProvider,
        client: httpx.AsyncClient,
        api_url: str = "https://api.github.com",
        main_branch: str = "main",
    ) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise PublicationError("GITHUB_REPOSITORY must use owner/name format")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", main_branch):
            raise PublicationError("GitHub main branch is invalid")
        self._repository = repository
        self._owner = repository.split("/", 1)[0]
        self._token_provider = token_provider
        self._client = client
        self._api_url = api_url.rstrip("/")
        self._main_branch = main_branch

    async def publish(self, request: PublicationRequest) -> PublicationResult:
        token = await self._token_provider.get_token()
        headers = {**_API_HEADERS, "Authorization": f"Bearer {token}"}
        branch = _branch_name(request)
        marker = _publication_marker(request)

        # 固定分支名与 marker 共同提供幂等键，进程重试时不会重复创建 PR。
        existing = await self._find_existing_pull(branch, marker, headers)
        if existing is not None:
            return existing

        current_sha = await self._read_ref_sha(self._main_branch, headers)
        if current_sha != request.validation.base_sha:
            return PublicationResult(
                disposition=PublicationDisposition.STALE_BASE,
                branch=branch,
            )

        branch_sha = await self._read_ref_sha(branch, headers, missing_ok=True)
        if branch_sha is None:
            branch_sha = await self._create_commit_and_branch(
                request,
                branch,
                marker,
                headers,
            )
        else:
            await self._verify_existing_branch(branch_sha, marker, headers)

        body = build_pull_request_body(request, marker=marker)
        try:
            payload = await self._request_object(
                "POST",
                f"/repos/{self._repository}/pulls",
                headers=headers,
                json={
                    "title": _pull_request_title(request),
                    "head": branch,
                    "base": self._main_branch,
                    "body": body,
                    "maintainer_can_modify": True,
                },
            )
        except PublicationConflictError:
            # 创建请求可能已在 GitHub 成功但响应丢失；按固定 marker 查询即可幂等恢复。
            recovered = await self._find_existing_pull(branch, marker, headers)
            if recovered is None:
                raise
            return recovered
        return _publication_result(payload, branch=branch, commit_sha=branch_sha)

    async def _create_commit_and_branch(
        self,
        request: PublicationRequest,
        branch: str,
        marker: str,
        headers: dict[str, str],
    ) -> str:
        base_commit = await self._request_object(
            "GET",
            f"/repos/{self._repository}/git/commits/{request.validation.base_sha}",
            headers=headers,
        )
        tree = base_commit.get("tree")
        if not isinstance(tree, dict) or not _valid_sha(tree.get("sha")):
            raise PublicationError("GitHub base commit response is invalid")

        entries: list[dict[str, object]] = []
        for item in request.files:
            if item.content is None:
                entries.append(
                    {"path": item.path, "mode": "100644", "type": "blob", "sha": None}
                )
                continue
            blob = await self._request_object(
                "POST",
                f"/repos/{self._repository}/git/blobs",
                headers=headers,
                json={
                    "content": base64.b64encode(item.content).decode("ascii"),
                    "encoding": "base64",
                },
            )
            blob_sha = blob.get("sha")
            if not _valid_sha(blob_sha):
                raise PublicationError("GitHub blob response is invalid")
            entries.append(
                {"path": item.path, "mode": "100644", "type": "blob", "sha": blob_sha}
            )

        created_tree = await self._request_object(
            "POST",
            f"/repos/{self._repository}/git/trees",
            headers=headers,
            json={"base_tree": tree["sha"], "tree": entries},
        )
        tree_sha = created_tree.get("sha")
        if not _valid_sha(tree_sha):
            raise PublicationError("GitHub tree response is invalid")
        commit = await self._request_object(
            "POST",
            f"/repos/{self._repository}/git/commits",
            headers=headers,
            json={
                "message": f"{_commit_title(request)}\n\n{marker}",
                "tree": tree_sha,
                "parents": [request.validation.base_sha],
            },
        )
        commit_sha = commit.get("sha")
        if not _valid_sha(commit_sha):
            raise PublicationError("GitHub commit response is invalid")
        try:
            await self._request_object(
                "POST",
                f"/repos/{self._repository}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            )
        except PublicationConflictError:
            recovered_sha = await self._read_ref_sha(branch, headers, missing_ok=True)
            if recovered_sha is None:
                raise
            await self._verify_existing_branch(recovered_sha, marker, headers)
            return recovered_sha
        return str(commit_sha)

    async def _verify_existing_branch(
        self,
        commit_sha: str,
        marker: str,
        headers: dict[str, str],
    ) -> None:
        commit = await self._request_object(
            "GET",
            f"/repos/{self._repository}/git/commits/{commit_sha}",
            headers=headers,
        )
        if marker not in str(commit.get("message", "")):
            raise PublicationConflictError(
                "deterministic publication branch contains a different patch"
            )

    async def _find_existing_pull(
        self,
        branch: str,
        marker: str,
        headers: dict[str, str],
    ) -> PublicationResult | None:
        pulls = await self._request_list(
            "GET",
            f"/repos/{self._repository}/pulls",
            headers=headers,
            params={
                "state": "all",
                "head": f"{self._owner}:{branch}",
                "base": self._main_branch,
                "per_page": "10",
            },
        )
        for pull in pulls:
            if marker in str(pull.get("body", "")):
                head = pull.get("head")
                commit_sha = head.get("sha") if isinstance(head, dict) else None
                return _publication_result(
                    pull,
                    branch=branch,
                    commit_sha=commit_sha,
                    reused=True,
                )
        if pulls:
            raise PublicationConflictError(
                "deterministic publication branch is already used by another patch"
            )
        return None

    async def _read_ref_sha(
        self,
        branch: str,
        headers: dict[str, str],
        *,
        missing_ok: bool = False,
    ) -> str | None:
        response = await self._request(
            "GET",
            f"/repos/{self._repository}/git/ref/heads/{branch}",
            headers=headers,
            allowed_statuses={404} if missing_ok else set(),
        )
        if response.status_code == 404:
            return None
        payload = _json_object(response)
        obj = payload.get("object")
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not _valid_sha(sha):
            raise PublicationError("GitHub ref response is invalid")
        return str(sha)

    async def _request_object(self, *args: object, **kwargs: object) -> dict[str, object]:
        return _json_object(await self._request(*args, **kwargs))

    async def _request_list(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
        response = await self._request(*args, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublicationError("GitHub returned invalid JSON") from exc
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise PublicationError("GitHub returned an unexpected response shape")
        return payload

    async def _request(
        self,
        method: object,
        path: object,
        *,
        headers: object,
        allowed_statuses: set[int] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                str(method),
                f"{self._api_url}{path}",
                headers=dict(headers),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise PublicationError(
                f"GitHub publication failed: {type(exc).__name__}"
            ) from exc
        if allowed_statuses and response.status_code in allowed_statuses:
            return response
        if response.status_code in {401, 403}:
            raise PublicationAuthenticationError(
                f"GitHub publication authentication failed with status {response.status_code}"
            )
        if response.status_code in {409, 422}:
            raise PublicationConflictError(
                f"GitHub publication conflict with status {response.status_code}"
            )
        if response.status_code >= 400:
            raise PublicationError(
                f"GitHub publication failed with status {response.status_code}"
            )
        return response


def build_pull_request_body(request: PublicationRequest, *, marker: str | None = None) -> str:
    """只使用结构化验证摘要生成正文，绝不拼接用户描述或 Markdown。"""

    validation = request.validation
    evidence = request.evidence
    checks = ", ".join(
        name for name, passed in validation.docx_validation.checks.items() if passed
    ) or "none"
    prompt_versions = ", ".join(
        f"{name}={version}" for name, version in sorted(evidence.prompt_versions.items())
    )
    trace = evidence.trace_url or f"trace-id: `{evidence.trace_id}`"
    visible_body = "\n".join(
        (
            "## Summary",
            "",
            f"Automated backend repair for `{evidence.category.value}` feedback "
            f"`{str(request.feedback_id)[:8]}`.",
            "",
            "## Deterministic verification",
            "",
            f"- Baseline failure reproduced: `{validation.baseline_reproduction.expected_failure_observed}`",
            f"- Target test passed: `{validation.target_validation.passed}`",
            f"- Full backend tests: `{validation.full_validation.tests}` tests, "
            f"`{validation.full_validation.failures}` failures, "
            f"`{validation.full_validation.errors}` errors, "
            f"`{validation.full_validation.skipped}` skipped",
            f"- DOCX checks: `{checks}`",
            f"- Changed files: `{', '.join(validation.changed_files)}`",
            "",
            "## Review metadata",
            "",
            f"- Risk: `{evidence.risk.value}`",
            f"- Extension sync required: `{evidence.extension_sync_required}`",
            f"- Graph / Policy: `{evidence.graph_version}` / `{evidence.policy_version}`",
            f"- Prompts: `{prompt_versions}`",
            f"- Provider / Model: `{evidence.provider}` / `{evidence.model}`",
            f"- Usage: {evidence.model_calls} model calls, {evidence.tool_calls} tool calls, "
            f"{evidence.total_tokens} tokens, estimated cost {evidence.estimated_cost}",
            f"- Trace: {trace}",
            f"- Base SHA: `{validation.base_sha}`",
            f"- Validated patch SHA-256: `{validation.validated_patch_sha256}`",
        )
    )
    # PublicationRequest 从结构上不含 contact/原始反馈；这里仍拦截邮箱、Bearer 与 Secret
    # 赋值。手机号规则不适用于 SHA/Token 计数等机器元数据，会把合法数字前缀误报为电话。
    if (
        mask_text(
            visible_body,
            max_length=len(visible_body),
            redact_phone=False,
        )
        != visible_body
    ):
        raise PublicationError("pull request body contains a sensitive pattern")
    body = f"{visible_body}\n\n{marker or _publication_marker(request)}"
    if len(body.encode("utf-8")) > 20_000:
        raise PublicationError("pull request body exceeds publication limit")
    return body


def _branch_name(request: PublicationRequest) -> str:
    return (
        f"agent/feedback-{str(request.feedback_id)[:8]}-"
        f"{request.evidence.category.value}"
    )


def _commit_title(request: PublicationRequest) -> str:
    return (
        f"fix: repair {request.evidence.category.value} for feedback "
        f"{str(request.feedback_id)[:8]}"
    )


def _pull_request_title(request: PublicationRequest) -> str:
    return _commit_title(request)


def _publication_marker(request: PublicationRequest) -> str:
    return (
        "<!-- mdtoword-agent "
        f"feedback={request.feedback_id} "
        f"patch={request.validation.validated_patch_sha256} -->"
    )


def _publication_result(
    payload: dict[str, object],
    *,
    branch: str,
    commit_sha: object,
    reused: bool = False,
) -> PublicationResult:
    number = payload.get("number")
    url = payload.get("html_url")
    if (
        not isinstance(number, int)
        or number < 1
        or not isinstance(url, str)
        or not url.startswith("https://")
        or not _valid_sha(commit_sha)
    ):
        raise PublicationError("GitHub pull request response is invalid")
    return PublicationResult(
        disposition=PublicationDisposition.PR_OPENED,
        branch=branch,
        commit_sha=str(commit_sha),
        pr_number=number,
        pr_url=url,
        reused=reused,
    )


def _json_object(
    response: httpx.Response,
    *,
    authentication: bool = False,
) -> dict[str, object]:
    error = PublicationAuthenticationError if authentication else PublicationError
    try:
        payload = response.json()
    except ValueError as exc:
        raise error("GitHub returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise error("GitHub returned an unexpected response shape")
    return payload


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None
