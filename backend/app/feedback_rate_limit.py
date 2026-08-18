import asyncio
import ipaddress
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass


MINUTE_SECONDS = 60.0
HOUR_SECONDS = 60.0 * 60.0
DAY_SECONDS = 24.0 * HOUR_SECONDS


class ClientIpUnavailableError(ValueError):
    """请求中没有可用于生产限流的可信公网 IP。"""


def resolve_cloudflare_client_ip(raw_value: str | None) -> str:
    """规范 Cloudflare 单值客户端 IP；不解析可由调用方拼接的转发链。"""

    if raw_value is None:
        raise ClientIpUnavailableError("trusted client IP header is missing")

    candidate = raw_value.strip()
    if not candidate or "," in candidate:
        raise ClientIpUnavailableError("trusted client IP must contain one address")

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise ClientIpUnavailableError("trusted client IP is invalid") from exc

    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped

    # 生产请求经过 Cloudflare 后应保留公网来源；拒绝私网/保留地址可避免把代理地址
    # 错当成所有用户共享的限流身份。
    if not address.is_global:
        raise ClientIpUnavailableError("trusted client IP is not globally routable")

    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


@dataclass(frozen=True)
class FeedbackRateLimitPolicy:
    per_minute: int = 1
    per_hour: int = 5
    per_day: int = 10
    global_per_hour: int = 30

    def __post_init__(self) -> None:
        if min(
            self.per_minute,
            self.per_hour,
            self.per_day,
            self.global_per_hour,
        ) < 1:
            raise ValueError("feedback rate limits must be positive")


@dataclass(frozen=True)
class FeedbackRateLimitDecision:
    allowed: bool
    retry_after_seconds: int | None = None


class FeedbackRateLimiter:
    """单进程反馈滑动窗口；多 worker/实例部署前必须替换为共享存储。"""

    def __init__(
        self,
        policy: FeedbackRateLimitPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_ip_keys: int = 10_000,
        cleanup_interval: int = 100,
    ) -> None:
        if max_ip_keys < 1 or cleanup_interval < 1:
            raise ValueError("feedback limiter capacity must be positive")
        self._policy = policy
        self._clock = clock
        self._max_ip_keys = max_ip_keys
        self._cleanup_interval = cleanup_interval
        self._events_by_ip: OrderedDict[str, deque[float]] = OrderedDict()
        self._global_events: deque[float] = deque()
        self._consume_count = 0
        self._lock = asyncio.Lock()

    async def consume(self, ip_key: str) -> FeedbackRateLimitDecision:
        if not ip_key:
            raise ValueError("feedback limiter IP key must not be empty")

        now = self._clock()
        async with self._lock:
            # 锁只覆盖内存中的“检查并消费”，Supabase 网络 I/O 必须在释放锁后执行，
            # 否则一个慢写入会阻塞所有用户的反馈请求。
            self._consume_count += 1
            self._prune_deque(self._global_events, now - HOUR_SECONDS)
            if self._consume_count % self._cleanup_interval == 0:
                self._prune_inactive_ips(now)

            events = self._events_by_ip.get(ip_key)
            if events is None:
                self._make_room_for_ip(now)
                events = deque()
                self._events_by_ip[ip_key] = events
            else:
                self._events_by_ip.move_to_end(ip_key)
            self._prune_deque(events, now - DAY_SECONDS)

            retry_after = self._retry_after(events, now)
            if retry_after is not None:
                return FeedbackRateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after,
                )

            events.append(now)
            self._global_events.append(now)
            return FeedbackRateLimitDecision(allowed=True)

    def _retry_after(self, events: deque[float], now: float) -> int | None:
        waits = [
            self._window_wait(
                events,
                now=now,
                window_seconds=MINUTE_SECONDS,
                limit=self._policy.per_minute,
            ),
            self._window_wait(
                events,
                now=now,
                window_seconds=HOUR_SECONDS,
                limit=self._policy.per_hour,
            ),
            self._window_wait(
                events,
                now=now,
                window_seconds=DAY_SECONDS,
                limit=self._policy.per_day,
            ),
            self._window_wait(
                self._global_events,
                now=now,
                window_seconds=HOUR_SECONDS,
                limit=self._policy.global_per_hour,
            ),
        ]
        active_waits = [wait for wait in waits if wait > 0]
        if not active_waits:
            return None
        return max(1, math.ceil(max(active_waits)))

    @staticmethod
    def _window_wait(
        events: deque[float],
        *,
        now: float,
        window_seconds: float,
        limit: int,
    ) -> float:
        recent = [value for value in events if value > now - window_seconds]
        if len(recent) < limit:
            return 0.0
        # 若运行时阈值收紧，应等到足够多的旧事件退出，而不是只等最早一个。
        return max(0.0, recent[-limit] + window_seconds - now)

    def _prune_inactive_ips(self, now: float) -> None:
        cutoff = now - DAY_SECONDS
        for ip_key in tuple(self._events_by_ip):
            events = self._events_by_ip[ip_key]
            self._prune_deque(events, cutoff)
            if not events:
                del self._events_by_ip[ip_key]

    def _make_room_for_ip(self, now: float) -> None:
        if len(self._events_by_ip) < self._max_ip_keys:
            return
        self._prune_inactive_ips(now)
        if len(self._events_by_ip) >= self._max_ip_keys:
            self._events_by_ip.popitem(last=False)

    @staticmethod
    def _prune_deque(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()
