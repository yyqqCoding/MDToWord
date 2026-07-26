"""SupabaseFeedbackRepository 单测:用 httpx.MockTransport,不发真实请求。"""

import json
from uuid import uuid4

import httpx
import pytest

from agent.exceptions import SupabaseError
from agent.feedback_repository import RETRYABLE_ATTEMPTS, SupabaseFeedbackRepository


def make_repository(handler):
    calls = {"count": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return handler(request)

    repository = SupabaseFeedbackRepository(
        "https://example.supabase.co", "test-key",
        transport=httpx.MockTransport(counting_handler),
        sleep=lambda seconds: None,
    )
    return repository, calls


def feedback_row(feedback_id):
    return {
        "id": str(feedback_id), "feedback_type": "bug",
        "markdown_content": "# md", "description": "desc",
        "contact": "user@example.com", "status": "pending", "attempt_count": 0,
    }


def test_401_maps_to_unauthorized_and_does_not_retry():
    repository, calls = make_repository(
        lambda request: httpx.Response(401, json={"message": "bad key"}))
    with pytest.raises(SupabaseError) as exc_info:
        repository.get_feedback(uuid4())
    assert exc_info.value.error_code == "supabase_unauthorized"
    assert calls["count"] == 1


def test_429_maps_to_rate_limited_and_retries():
    repository, calls = make_repository(
        lambda request: httpx.Response(429, json={"message": "slow down"}))
    with pytest.raises(SupabaseError) as exc_info:
        repository.get_feedback(uuid4())
    assert exc_info.value.error_code == "supabase_rate_limited"
    assert calls["count"] == RETRYABLE_ATTEMPTS


def test_500_maps_to_server_error_and_retries():
    repository, calls = make_repository(
        lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(SupabaseError) as exc_info:
        repository.get_feedback(uuid4())
    assert exc_info.value.error_code == "supabase_server_error"
    assert calls["count"] == RETRYABLE_ATTEMPTS


def test_transient_500_then_success_recovers():
    feedback_id = uuid4()
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state.pop("first", False):
            return httpx.Response(503, text="warming up")
        return httpx.Response(200, json=[feedback_row(feedback_id)])

    repository, calls = make_repository(handler)
    feedback = repository.get_feedback(feedback_id)
    assert feedback is not None and feedback.id == feedback_id
    assert calls["count"] == 2


def test_error_body_is_truncated():
    repository, _ = make_repository(
        lambda request: httpx.Response(400, text="x" * 10_000))
    with pytest.raises(SupabaseError) as exc_info:
        repository.get_feedback(uuid4())
    assert len(exc_info.value.message) < 1_000


def test_auth_headers_built_inside_repository_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.headers.get("apikey")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    repository, _ = make_repository(handler)
    repository.get_feedback(uuid4())
    assert seen["apikey"] == "test-key"
    assert seen["authorization"] == "Bearer test-key"


def test_claim_feedback_posts_rpc_payload_and_parses_row():
    feedback_id, claim_token = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/claim_feedback"
        payload = json.loads(request.content)
        assert payload == {
            "p_feedback_id": str(feedback_id),
            "p_claim_token": str(claim_token),
            "p_max_attempts": 3,
        }
        row = feedback_row(feedback_id) | {"status": "claimed", "attempt_count": 1}
        return httpx.Response(200, json=[row])

    repository, _ = make_repository(handler)
    claimed = repository.claim_feedback(feedback_id, claim_token)
    assert claimed is not None
    assert claimed.status == "claimed"


def test_claim_feedback_empty_result_returns_none():
    repository, _ = make_repository(lambda request: httpx.Response(200, json=[]))
    assert repository.claim_feedback(uuid4(), uuid4()) is None


def test_create_run_returns_uuid():
    run_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/agent_runs"
        assert request.headers.get("prefer") == "return=representation"
        return httpx.Response(201, json=[{"id": str(run_id)}])

    repository, _ = make_repository(handler)
    assert repository.create_run(uuid4(), provider="openai_compatible",
                                 model="deepseek-chat") == run_id
