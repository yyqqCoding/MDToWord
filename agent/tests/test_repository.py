import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from agent.domain.enums import FeedbackStatus, FeedbackType
from agent.domain.errors import (
    ClaimTokenMismatchError,
    FeedbackNotFoundError,
    RepositoryError,
)
from agent.domain.models import FeedbackRecord
from agent.repositories.fake import FakeFeedbackRepository
from agent.repositories.supabase import SupabaseFeedbackRepository


def make_feedback(*, created_at: datetime | None = None) -> FeedbackRecord:
    now = created_at or datetime.now(UTC)
    return FeedbackRecord(
        id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="| A | B |\n|---|---|\n| 1 | 2 |",
        description="Word 中没有表格",
        contact="user@example.com",
        created_at=now,
        updated_at=now,
    )


def test_concurrent_claim_has_exactly_one_winner():
    async def scenario():
        repository = FakeFeedbackRepository([make_feedback()])
        now = datetime.now(UTC)
        return await asyncio.gather(
            repository.claim_next(now=now, lease_seconds=60, max_attempts=2),
            repository.claim_next(now=now, lease_seconds=60, max_attempts=2),
        )

    first, second = asyncio.run(scenario())

    winners = [result for result in (first, second) if result is not None]
    assert len(winners) == 1
    assert winners[0].status is FeedbackStatus.CLAIMED
    assert winners[0].attempt_count == 1
    assert winners[0].claim_token is not None


def test_expired_claim_is_reclaimed_with_a_new_token():
    async def scenario():
        repository = FakeFeedbackRepository([make_feedback()])
        started = datetime(2026, 8, 10, tzinfo=UTC)
        first = await repository.claim_next(now=started, lease_seconds=60, max_attempts=2)
        reclaimed = await repository.claim_next(
            now=started + timedelta(seconds=61),
            lease_seconds=60,
            max_attempts=2,
        )
        return first, reclaimed

    first, reclaimed = asyncio.run(scenario())

    assert first is not None and reclaimed is not None
    assert reclaimed.claim_token != first.claim_token
    assert reclaimed.attempt_count == 2


def test_claim_by_id_only_claims_the_requested_feedback():
    async def scenario():
        first = make_feedback()
        second = make_feedback(created_at=first.created_at)
        repository = FakeFeedbackRepository([first, second])
        claimed = await repository.claim_by_id(
            second.id,
            now=datetime.now(UTC),
            lease_seconds=60,
            max_attempts=2,
        )
        return first, claimed, await repository.get(first.id)

    first, claimed, untouched = asyncio.run(scenario())

    assert claimed is not None
    assert claimed.id != first.id
    assert claimed.status is FeedbackStatus.CLAIMED
    assert untouched is not None
    assert untouched.status is FeedbackStatus.PENDING


def test_expired_claim_over_attempt_limit_moves_to_needs_human():
    async def scenario():
        feedback = make_feedback()
        repository = FakeFeedbackRepository([feedback])
        started = datetime(2026, 8, 10, tzinfo=UTC)
        await repository.claim_next(now=started, lease_seconds=60, max_attempts=1)
        result = await repository.claim_next(
            now=started + timedelta(seconds=61),
            lease_seconds=60,
            max_attempts=1,
        )
        return result, await repository.get(feedback.id)

    result, stored = asyncio.run(scenario())

    assert result is None
    assert stored is not None
    assert stored.status is FeedbackStatus.NEEDS_HUMAN
    assert stored.last_error_code == "claim_attempts_exhausted"


def test_transition_requires_the_active_claim_token():
    async def scenario():
        feedback = make_feedback()
        repository = FakeFeedbackRepository([feedback])
        claimed = await repository.claim_next(
            now=datetime.now(UTC),
            lease_seconds=60,
            max_attempts=2,
        )
        assert claimed is not None
        with pytest.raises(ClaimTokenMismatchError):
            await repository.transition(
                feedback.id,
                claim_token=uuid4(),
                target=FeedbackStatus.GATING,
            )

    asyncio.run(scenario())


def test_transition_of_missing_feedback_is_explicit_error():
    async def scenario():
        repository = FakeFeedbackRepository()
        with pytest.raises(FeedbackNotFoundError):
            await repository.transition(
                uuid4(),
                claim_token=uuid4(),
                target=FeedbackStatus.GATING,
            )

    asyncio.run(scenario())


def test_pending_feedback_at_attempt_limit_moves_to_needs_human():
    async def scenario():
        feedback = make_feedback().model_copy(update={"attempt_count": 2})
        repository = FakeFeedbackRepository([feedback])
        result = await repository.claim_next(
            now=datetime.now(UTC),
            lease_seconds=60,
            max_attempts=2,
        )
        return result, await repository.get(feedback.id)

    result, stored = asyncio.run(scenario())

    assert result is None
    assert stored is not None
    assert stored.status is FeedbackStatus.NEEDS_HUMAN


def test_supabase_claim_uses_atomic_rpc_and_parses_feedback():
    feedback = make_feedback()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/claim_next_agent_feedback"
        assert request.headers["authorization"] == "Bearer agent-secret"
        request_payload = json.loads(request.content)
        claimed = feedback.model_copy(
            update={
                "status": FeedbackStatus.CLAIMED,
                "attempt_count": 1,
                "claim_token": UUID(request_payload["p_claim_token"]),
                "claimed_at": datetime.now(UTC),
            }
        )
        return httpx.Response(200, json=[claimed.model_dump(mode="json")])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseFeedbackRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.claim_next(
                now=datetime.now(UTC),
                lease_seconds=300,
                max_attempts=3,
            )

    claimed = asyncio.run(scenario())

    assert claimed is not None
    assert claimed.status is FeedbackStatus.CLAIMED
    assert claimed.claim_token is not None


def test_supabase_claim_by_id_uses_targeted_atomic_rpc():
    feedback = make_feedback()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/rpc/claim_agent_feedback"
        payload = json.loads(request.content)
        assert payload["p_feedback_id"] == str(feedback.id)
        claimed = feedback.model_copy(
            update={
                "status": FeedbackStatus.CLAIMED,
                "attempt_count": 1,
                "claim_token": UUID(payload["p_claim_token"]),
                "claimed_at": datetime.now(UTC),
            }
        )
        row = claimed.model_dump(mode="json")
        row.update(
            {
                "automatable": None,
                "agent_approved": False,
                "expected_behavior": None,
                "source_version": None,
                "last_error": None,
                "resolution_type": None,
            }
        )
        return httpx.Response(200, json=[row])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseFeedbackRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.claim_by_id(
                feedback.id,
                now=datetime.now(UTC),
                lease_seconds=300,
                max_attempts=3,
            )

    claimed = asyncio.run(scenario())

    assert claimed is not None
    assert claimed.id == feedback.id


def test_supabase_get_ignores_legacy_database_columns():
    feedback = make_feedback()

    async def handler(request: httpx.Request) -> httpx.Response:
        row = feedback.model_dump(mode="json")
        row["automatable"] = True
        row["expected_behavior"] = "legacy content must not enter domain model"
        return httpx.Response(200, json=[row])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseFeedbackRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.get(feedback.id)

    stored = asyncio.run(scenario())

    assert stored is not None
    assert stored.id == feedback.id
    assert "automatable" not in stored.model_dump()


def test_supabase_error_does_not_echo_response_or_credentials():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="contact=user@example.com Authorization=agent-secret",
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseFeedbackRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            with pytest.raises(RepositoryError) as exc_info:
                await repository.get(uuid4())
            return str(exc_info.value)

    message = asyncio.run(scenario())

    assert "500" in message
    assert "agent-secret" not in message
    assert "user@example.com" not in message


def test_duplicate_lookup_only_matches_open_processing_results():
    async def scenario():
        active = make_feedback()
        closed = make_feedback(created_at=active.created_at + timedelta(seconds=1)).model_copy(
            update={
                "status": FeedbackStatus.CANNOT_REPRODUCE,
                "content_fingerprint": active.content_fingerprint,
            }
        )
        repository = FakeFeedbackRepository([active, closed])
        match = await repository.find_open_by_fingerprint(
            active.content_fingerprint,
            excluding_feedback_id=closed.id,
        )
        no_match = await repository.find_open_by_fingerprint(
            closed.content_fingerprint,
            excluding_feedback_id=active.id,
        )
        return match, no_match

    match, no_match = asyncio.run(scenario())

    assert match is not None
    assert no_match is None


def test_stale_base_can_only_be_requeued_once():
    async def scenario():
        feedback = make_feedback().model_copy(
            update={
                "status": FeedbackStatus.STALE_BASE,
                "claim_token": uuid4(),
            }
        )
        repository = FakeFeedbackRepository([feedback])
        requeued = await repository.transition(
            feedback.id,
            claim_token=feedback.claim_token,
            target=FeedbackStatus.PENDING,
        )
        return requeued

    requeued = asyncio.run(scenario())

    assert requeued.stale_requeue_count == 1
    assert requeued.claim_token is None
