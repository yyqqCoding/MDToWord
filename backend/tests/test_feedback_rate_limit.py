import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.feedback_rate_limit import (
    ClientIpUnavailableError,
    FeedbackRateLimiter,
    FeedbackRateLimitPolicy,
    resolve_cloudflare_client_ip,
)
from app.main import FeedbackStorageError, app


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


FEEDBACK_BODY = {
    "feedback_type": "bug",
    "markdown_content": "$x$",
    "description": "公式导出错误",
    "contact": "",
}


def test_resolve_cloudflare_client_ip_normalizes_address_family() -> None:
    assert resolve_cloudflare_client_ip("8.8.8.8") == "8.8.8.8"
    assert resolve_cloudflare_client_ip("::ffff:8.8.8.8") == "8.8.8.8"
    assert (
        resolve_cloudflare_client_ip("2606:4700:4700::1111")
        == "2606:4700:4700::/64"
    )


@pytest.mark.parametrize(
    "raw_value",
    [
        None,
        "",
        "8.8.8.8, 1.1.1.1",
        "not-an-ip",
        "127.0.0.1",
        "10.0.0.1",
        "2001:db8::1",
    ],
)
def test_resolve_cloudflare_client_ip_rejects_untrusted_values(
    raw_value: str | None,
) -> None:
    with pytest.raises(ClientIpUnavailableError):
        resolve_cloudflare_client_ip(raw_value)


def test_limiter_enforces_minute_window_and_exact_retry_after() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        limiter = FeedbackRateLimiter(FeedbackRateLimitPolicy(), clock=clock)

        assert (await limiter.consume("8.8.8.8")).allowed is True
        blocked = await limiter.consume("8.8.8.8")
        assert blocked.allowed is False
        assert blocked.retry_after_seconds == 60

        clock.advance(60)
        assert (await limiter.consume("8.8.8.8")).allowed is True

    asyncio.run(scenario())


def test_limiter_enforces_hour_and_day_windows() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        hourly = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(
                per_minute=100,
                per_hour=5,
                per_day=100,
                global_per_hour=100,
            ),
            clock=clock,
        )
        for _ in range(5):
            assert (await hourly.consume("8.8.8.8")).allowed is True
            clock.advance(60)
        blocked_hour = await hourly.consume("8.8.8.8")
        assert blocked_hour.allowed is False
        assert blocked_hour.retry_after_seconds == 3300

        clock = ManualClock()
        daily = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(
                per_minute=100,
                per_hour=100,
                per_day=10,
                global_per_hour=100,
            ),
            clock=clock,
        )
        for _ in range(10):
            assert (await daily.consume("8.8.8.8")).allowed is True
            clock.advance(3600)
        blocked_day = await daily.consume("8.8.8.8")
        assert blocked_day.allowed is False
        assert blocked_day.retry_after_seconds == 14 * 3600

    asyncio.run(scenario())


def test_limiter_enforces_global_window_across_ips() -> None:
    async def scenario() -> None:
        limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(
                per_minute=100,
                per_hour=100,
                per_day=100,
                global_per_hour=2,
            ),
            clock=ManualClock(),
        )

        assert (await limiter.consume("8.8.8.8")).allowed is True
        assert (await limiter.consume("1.1.1.1")).allowed is True
        blocked = await limiter.consume("9.9.9.9")
        assert blocked.allowed is False
        assert blocked.retry_after_seconds == 3600

    asyncio.run(scenario())


def test_limiter_allows_only_one_concurrent_request_for_same_ip() -> None:
    async def scenario() -> None:
        limiter = FeedbackRateLimiter(FeedbackRateLimitPolicy())
        results = await asyncio.gather(
            *(limiter.consume("8.8.8.8") for _ in range(20))
        )
        assert sum(result.allowed for result in results) == 1

    asyncio.run(scenario())


def test_limiter_cleans_expired_ip_entries() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(
                per_minute=100,
                per_hour=100,
                per_day=100,
                global_per_hour=100,
            ),
            clock=clock,
            max_ip_keys=2,
            cleanup_interval=1,
        )
        await limiter.consume("8.8.8.8")
        await limiter.consume("1.1.1.1")
        clock.advance(24 * 3600)
        await limiter.consume("9.9.9.9")

        assert list(limiter._events_by_ip) == ["9.9.9.9"]

    asyncio.run(scenario())


def test_feedback_endpoint_fails_closed_without_trusted_ip() -> None:
    async def scenario() -> None:
        app.state.feedback_rate_limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(),
            clock=ManualClock(),
        )
        transport = httpx.ASGITransport(app=app)
        with patch("app.main._insert_feedback", new_callable=AsyncMock) as insert:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post("/feedback", json=FEEDBACK_BODY)

        assert response.status_code == 503
        assert response.json()["error"] == "client_ip_unavailable"
        insert.assert_not_awaited()

    asyncio.run(scenario())


def test_feedback_endpoint_returns_429_without_second_write() -> None:
    async def scenario() -> None:
        app.state.feedback_rate_limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(),
            clock=ManualClock(),
        )
        transport = httpx.ASGITransport(app=app)
        with patch("app.main._insert_feedback", new_callable=AsyncMock) as insert:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                headers = {
                    "CF-Connecting-IP": "8.8.8.8",
                    "Origin": "chrome-extension://example",
                }
                accepted = await client.post(
                    "/feedback",
                    json=FEEDBACK_BODY,
                    headers=headers,
                )
                blocked = await client.post(
                    "/feedback",
                    json=FEEDBACK_BODY,
                    headers=headers,
                )

        assert accepted.status_code == 200
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        assert blocked.headers["Cache-Control"] == "no-store"
        assert blocked.headers["Access-Control-Expose-Headers"] == "Retry-After"
        assert insert.await_count == 1

    asyncio.run(scenario())


def test_feedback_storage_failure_keeps_consumed_quota() -> None:
    async def scenario() -> None:
        app.state.feedback_rate_limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy(),
            clock=ManualClock(),
        )
        transport = httpx.ASGITransport(app=app)
        storage_error = FeedbackStorageError("unavailable")
        with patch(
            "app.main._insert_feedback",
            new_callable=AsyncMock,
            side_effect=storage_error,
        ) as insert:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                headers = {"CF-Connecting-IP": "8.8.8.8"}
                failed = await client.post(
                    "/feedback",
                    json=FEEDBACK_BODY,
                    headers=headers,
                )
                blocked = await client.post(
                    "/feedback",
                    json=FEEDBACK_BODY,
                    headers=headers,
                )

        assert failed.status_code == 502
        assert blocked.status_code == 429
        assert insert.await_count == 1

    asyncio.run(scenario())


def test_feedback_storage_io_does_not_hold_limiter_lock() -> None:
    async def scenario() -> None:
        app.state.feedback_rate_limiter = FeedbackRateLimiter(
            FeedbackRateLimitPolicy()
        )
        first_storage_started = asyncio.Event()
        release_first_storage = asyncio.Event()

        async def blocking_insert(payload: dict[str, str]) -> None:
            first_storage_started.set()
            await release_first_storage.wait()

        transport = httpx.ASGITransport(app=app)
        with patch("app.main._insert_feedback", side_effect=blocking_insert):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                headers = {"CF-Connecting-IP": "8.8.8.8"}
                first = asyncio.create_task(
                    client.post("/feedback", json=FEEDBACK_BODY, headers=headers)
                )
                await first_storage_started.wait()
                second = await asyncio.wait_for(
                    client.post("/feedback", json=FEEDBACK_BODY, headers=headers),
                    timeout=0.5,
                )
                assert second.status_code == 429
                release_first_storage.set()
                assert (await first).status_code == 200

    asyncio.run(scenario())
