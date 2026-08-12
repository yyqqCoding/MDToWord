import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from agent.domain.enums import GateCategory, RiskLevel
from agent.domain.errors import PublicationAuthenticationError
from agent.domain.repair import (
    BaselineReproductionValidation,
    DocxValidation,
    FullValidation,
    TargetValidation,
    ValidationResult,
)
from agent.publishing.contracts import (
    PublicationDisposition,
    PublicationEvidence,
    PublicationFile,
    PublicationRequest,
)
from agent.publishing.github import GitHubAppTokenProvider, GitHubPullRequestPublisher
from agent.publishing import check as check_module


BASE_SHA = "a" * 40
COMMIT_SHA = "d" * 40
PATCH = b"deterministic validated patch"


class StaticTokenProvider:
    async def get_token(self) -> str:
        return "installation-token"


def make_request(*, patch: bytes = PATCH) -> PublicationRequest:
    validation = ValidationResult(
        passed=True,
        base_sha=BASE_SHA,
        source_snapshot_sha256="1" * 64,
        test_patch_sha256="2" * 64,
        fix_patch_sha256="3" * 64,
        target_test_selector="test_feedback_table",
        baseline_reproduction=BaselineReproductionValidation(
            executed=True,
            expected_failure_observed=True,
        ),
        target_validation=TargetValidation(passed=True),
        full_validation=FullValidation(
            passed=True,
            tests=44,
            failures=0,
            errors=0,
            skipped=0,
            baseline_skipped=0,
        ),
        docx_validation=DocxValidation(
            passed=True,
            checks={"minimum_table_count": True},
        ),
        changed_files=("backend/app/normalizer.py",),
        validated_patch_ref="artifact://run/validated.patch",
        validated_patch_sha256=hashlib.sha256(PATCH).hexdigest(),
    )
    return PublicationRequest(
        feedback_id=uuid4(),
        validation=validation,
        validated_patch=patch,
        files=(
            PublicationFile(
                path="backend/app/normalizer.py",
                content=b"def normalize():\n    return True\n",
            ),
        ),
        evidence=PublicationEvidence(
            category=GateCategory.TABLE_PARSING,
            risk=RiskLevel.LOW,
            graph_version="agent-graph-v6",
            policy_version="publication-policy-v1",
            prompt_versions={"gate": "gate-v3"},
            provider="openai_compatible",
            model="configured-model",
            model_calls=3,
            tool_calls=9,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            estimated_cost=str(Decimal("0.001")),
            trace_id="trace-id",
            trace_url="https://cloud.langfuse.com/trace/trace-id",
        ),
    )


def test_publisher_creates_single_commit_branch_and_pull_request():
    request = make_request()
    branch = f"agent/feedback-{str(request.feedback_id)[:8]}-table_parsing"
    requests: list[tuple[str, str]] = []
    published_body = ""

    async def handler(incoming: httpx.Request) -> httpx.Response:
        nonlocal published_body
        requests.append((incoming.method, incoming.url.path))
        assert incoming.headers["Authorization"] == "Bearer installation-token"
        path = incoming.url.path
        if path.endswith("/pulls") and incoming.method == "GET":
            return httpx.Response(200, json=[])
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": BASE_SHA}})
        if path.endswith(f"/git/ref/heads/{branch}"):
            return httpx.Response(404, json={})
        if path.endswith(f"/git/commits/{BASE_SHA}"):
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        if path.endswith("/git/blobs"):
            payload = json.loads(incoming.content)
            assert payload["encoding"] == "base64"
            return httpx.Response(201, json={"sha": "c" * 40})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "e" * 40})
        if path.endswith("/git/commits"):
            payload = json.loads(incoming.content)
            assert request.validation.validated_patch_sha256 in payload["message"]
            return httpx.Response(201, json={"sha": COMMIT_SHA})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": f"refs/heads/{branch}"})
        if path.endswith("/pulls") and incoming.method == "POST":
            payload = json.loads(incoming.content)
            published_body = payload["body"]
            assert payload["head"] == branch
            return httpx.Response(
                201,
                json={
                    "number": 7,
                    "html_url": "https://github.com/example/md-to-word/pull/7",
                },
            )
        raise AssertionError(f"unexpected request: {incoming.method} {path}")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = GitHubPullRequestPublisher(
                "example/md-to-word",
                token_provider=StaticTokenProvider(),
                client=client,
            )
            return await publisher.publish(request)

    result = asyncio.run(scenario())

    assert result.disposition is PublicationDisposition.PR_OPENED
    assert result.commit_sha == COMMIT_SHA
    assert result.pr_number == 7
    assert result.reused is False
    assert "user@example.com" not in published_body
    assert "markdown_content" not in published_body
    assert request.validation.validated_patch_sha256 in published_body
    assert ("POST", "/repos/example/md-to-word/pulls") in requests


def test_publisher_stops_before_side_effect_when_main_is_stale():
    request = make_request()
    methods: list[str] = []

    async def handler(incoming: httpx.Request) -> httpx.Response:
        methods.append(incoming.method)
        if incoming.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"object": {"sha": "f" * 40}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = GitHubPullRequestPublisher(
                "example/md-to-word",
                token_provider=StaticTokenProvider(),
                client=client,
            )
            return await publisher.publish(request)

    result = asyncio.run(scenario())

    assert result.disposition is PublicationDisposition.STALE_BASE
    assert methods == ["GET", "GET"]


def test_publisher_reuses_matching_pull_request_before_stale_check():
    request = make_request()
    marker = (
        f"<!-- mdtoword-agent feedback={request.feedback_id} "
        f"patch={request.validation.validated_patch_sha256} -->"
    )
    calls = 0

    async def handler(incoming: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert incoming.url.path.endswith("/pulls")
        return httpx.Response(
            200,
            json=[
                {
                    "number": 11,
                    "html_url": "https://github.com/example/md-to-word/pull/11",
                    "body": marker,
                    "head": {"sha": COMMIT_SHA},
                }
            ],
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = GitHubPullRequestPublisher(
                "example/md-to-word",
                token_provider=StaticTokenProvider(),
                client=client,
            )
            return await publisher.publish(request)

    result = asyncio.run(scenario())

    assert result.reused is True
    assert result.pr_number == 11
    assert calls == 1


def test_publication_request_rejects_hash_mismatch_before_github():
    with pytest.raises(ValidationError, match="patch hash"):
        make_request(patch=b"different")


def test_github_app_provider_signs_and_caches_short_lived_installation_token():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    requests: list[str] = []

    async def handler(incoming: httpx.Request) -> httpx.Response:
        requests.append(incoming.url.path)
        encoded = incoming.headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(encoded, options={"verify_signature": False})
        assert claims["iss"] == "12345"
        if incoming.url.path.endswith("/installation"):
            return httpx.Response(200, json={"id": 99})
        request = json.loads(incoming.content)
        assert request == {
            "repositories": ["md-to-word"],
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
            },
        }
        return httpx.Response(
            201,
            json={
                "token": "short-lived-token",
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "permissions": {
                    "contents": "write",
                    "pull_requests": "write",
                },
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GitHubAppTokenProvider(
                "example/md-to-word",
                app_id="12345",
                private_key=pem,
                client=client,
                now=lambda: now,
            )
            return await provider.get_token(), await provider.get_token()

    first, second = asyncio.run(scenario())

    assert first == second == "short-lived-token"
    assert requests == [
        "/repos/example/md-to-word/installation",
        "/app/installations/99/access_tokens",
    ]


def test_github_app_check_outputs_only_stable_status(monkeypatch, capsys):
    async def successful_check(config):
        assert config.supabase_url == "https://example.supabase.co"

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_AGENT_KEY", "not-printed")
    monkeypatch.setattr(check_module, "check_github_app", successful_check)

    assert check_module.main() == 0
    assert json.loads(capsys.readouterr().out) == {"status": "github_app_ready"}


def test_github_app_check_does_not_echo_sensitive_error(monkeypatch, capsys):
    async def failed_check(config):
        raise PublicationAuthenticationError("private-key-content")

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_AGENT_KEY", "not-printed")
    monkeypatch.setattr(check_module, "check_github_app", failed_check)

    assert check_module.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "publication_auth_error"}
    assert "private-key-content" not in output
