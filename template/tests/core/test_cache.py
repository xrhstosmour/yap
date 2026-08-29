"""Tests for the CacheService module."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError
from redis.exceptions import TimeoutError

from app.core.cache import CacheService
from app.core.cache import close_redis
from app.core.cache import get_cache
from app.core.cache import get_redis


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return an AsyncMock simulating a Redis client."""
    return AsyncMock()


@pytest.fixture
def cache(mock_redis: AsyncMock) -> CacheService:
    """Return a CacheService backed by the mocked Redis client."""
    return CacheService(mock_redis, prefix="test")


# _make_key


class TestMakeKey:
    """Tests for the _make_key helper."""

    def test_returns_prefixed_key(self, cache: CacheService) -> None:
        """_make_key should prefix the raw key with the service prefix."""
        assert cache._make_key("mykey") == "test:mykey"

    def test_handles_empty_key(self, cache: CacheService) -> None:
        """_make_key should work with an empty string key."""
        assert cache._make_key("") == "test:"


# get


class TestGet:
    """Tests for CacheService.get()."""

    @pytest.mark.asyncio
    async def test_hit_returns_deserialized_value(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """When the key exists, the JSON value should be deserialized."""
        import json

        expected = {"hello": "world", "count": 42}
        mock_redis.get.return_value = json.dumps(expected)

        result = await cache.get("mykey")

        assert result == expected
        mock_redis.get.assert_called_once_with("test:mykey")

    @pytest.mark.asyncio
    async def test_miss_returns_none(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """When the key does not exist, get should return None."""
        mock_redis.get.return_value = None

        result = await cache.get("nonexistent")

        assert result is None
        mock_redis.get.assert_called_once_with("test:nonexistent")

    @pytest.mark.asyncio
    async def test_json_decode_error_returns_raw_value(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """When the stored value is not valid JSON, return the raw string."""
        mock_redis.get.return_value = "not-json!!!"

        result = await cache.get("broken")

        assert result == "not-json!!!"

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis ConnectionError should be caught and None returned."""
        mock_redis.get.side_effect = ConnectionError("connection refused")

        result = await cache.get("mykey")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_error_returns_none(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis TimeoutError should be caught and None returned."""
        mock_redis.get.side_effect = TimeoutError("timed out")

        result = await cache.get("mykey")

        assert result is None


# set


class TestSet:
    """Tests for CacheService.set()."""

    @pytest.mark.asyncio
    async def test_success_without_ttl(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """set should call redis.set when no TTL is given."""
        import json

        value = {"status": "ok"}
        result = await cache.set("mykey", value)

        assert result is True
        mock_redis.set.assert_called_once_with("test:mykey", json.dumps(value))

    @pytest.mark.asyncio
    async def test_success_with_int_ttl(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """set should call redis.setex when an int TTL is given."""
        import json

        value = {"status": "ok"}
        result = await cache.set("mykey", value, ttl=60)

        assert result is True
        mock_redis.setex.assert_called_once_with("test:mykey", 60, json.dumps(value))

    @pytest.mark.asyncio
    async def test_success_with_timedelta_ttl(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """set should convert a timedelta TTL to seconds and call setex."""
        import json

        value = {"status": "ok"}
        result = await cache.set("mykey", value, ttl=timedelta(minutes=5))

        assert result is True
        mock_redis.setex.assert_called_once_with("test:mykey", 300, json.dumps(value))

    @pytest.mark.asyncio
    async def test_oversized_value_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A value larger than CACHE_MAX_VALUE_SIZE should be rejected."""
        # CACHE_MAX_VALUE_SIZE defaults to 1 MiB; create a value that
        # will exceed it after JSON serialisation (string + quotes).
        huge_value = "x" * 1_048_577

        result = await cache.set("mykey", huge_value)

        assert result is False
        mock_redis.set.assert_not_called()
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis ConnectionError should be caught and False returned."""
        mock_redis.set.side_effect = ConnectionError("connection refused")

        result = await cache.set("mykey", "value")

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_error_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis TimeoutError should be caught and False returned."""
        mock_redis.set.side_effect = TimeoutError("timed out")

        result = await cache.set("mykey", "value")

        assert result is False


# get_or_set (stampede protection)


class FakeLockingRedis:
    """Minimal in-memory Redis fake supporting SET NX EX semantics.

    `AsyncMock` cannot model the atomicity `get_or_set()` relies on, so
    this fake serialises access behind an `asyncio.Lock` the same way a
    single Redis instance serialises commands, letting concurrency tests
    exercise real lock-contention behaviour instead of pre-programmed
    mock return values.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._mutex = asyncio.Lock()

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        async with self._mutex:
            if nx and key in self._store:
                return None
            self._store[key] = value
            return True

    async def get(self, key: str) -> str | None:
        async with self._mutex:
            return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        async with self._mutex:
            self._store[key] = value
            return True

    async def delete(self, key: str) -> int:
        async with self._mutex:
            return 1 if self._store.pop(key, None) is not None else 0


class TestGetOrSet:
    """Tests for CacheService.get_or_set()."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_compute(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A cache hit should return the cached value without calling compute."""
        import json

        mock_redis.get.return_value = json.dumps({"cached": True})
        compute = AsyncMock(return_value={"cached": False})

        result = await cache.get_or_set("mykey", compute)

        assert result == {"cached": True}
        compute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_miss_acquires_lock_computes_and_caches(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """On a miss, the lock winner computes, caches, and releases the lock."""
        mock_redis.get.return_value = None
        mock_redis.set.return_value = True
        compute = AsyncMock(return_value={"computed": True})

        result = await cache.get_or_set("mykey", compute, ttl=60)

        assert result == {"computed": True}
        compute.assert_awaited_once()
        mock_redis.set.assert_any_call("test:mykey:lock", "1", nx=True, ex=10)
        mock_redis.delete.assert_awaited_once_with("test:mykey:lock")

    @pytest.mark.asyncio
    async def test_lock_loser_retries_and_returns_winners_value(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A caller that loses the lock race should return the winner's value."""
        import json

        # First get() (initial check) misses; lock acquisition fails (another
        # caller holds it); next get() (retry) returns the winner's value.
        mock_redis.get.side_effect = [None, json.dumps({"from": "winner"})]
        mock_redis.set.return_value = None
        compute = AsyncMock(return_value={"from": "loser"})

        result = await cache.get_or_set(
            "mykey", compute, retry_interval=0.01, wait_timeout=1
        )

        assert result == {"from": "winner"}
        compute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_loser_falls_through_after_wait_timeout(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """If the winner never publishes a value, the loser computes its own."""
        mock_redis.get.return_value = None
        mock_redis.set.return_value = None
        compute = AsyncMock(return_value={"from": "loser"})

        result = await cache.get_or_set(
            "mykey", compute, retry_interval=0.01, wait_timeout=0.05
        )

        assert result == {"from": "loser"}
        compute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_error_falls_through_to_compute(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis error while acquiring the lock should not block computation."""
        mock_redis.get.return_value = None
        mock_redis.set.side_effect = ConnectionError("connection refused")
        compute = AsyncMock(return_value={"from": "fallback"})

        result = await cache.get_or_set("mykey", compute)

        assert result == {"from": "fallback"}
        compute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_misses_compute_only_once(self) -> None:
        """Concurrent callers racing a cache miss should compute exactly once.

        Uses `FakeLockingRedis` instead of `AsyncMock` because this test
        needs genuine lock contention across concurrently scheduled
        coroutines, not a scripted sequence of return values.
        """
        fake_redis = FakeLockingRedis()
        concurrent_cache = CacheService(fake_redis, prefix="test")
        call_count = 0

        async def compute() -> dict[str, int]:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return {"value": 42}

        results = await asyncio.gather(
            *[concurrent_cache.get_or_set("shared", compute, ttl=60) for _ in range(10)]
        )

        assert call_count == 1
        assert all(result == {"value": 42} for result in results)


# delete


class TestDelete:
    """Tests for CacheService.delete()."""

    @pytest.mark.asyncio
    async def test_success_returns_true(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """delete should forward to redis.delete and return True."""
        result = await cache.delete("mykey")

        assert result is True
        mock_redis.delete.assert_called_once_with("test:mykey")

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis ConnectionError should be caught and False returned."""
        mock_redis.delete.side_effect = ConnectionError("connection refused")

        result = await cache.delete("mykey")

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_error_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A Redis TimeoutError should be caught and False returned."""
        mock_redis.delete.side_effect = TimeoutError("timed out")

        result = await cache.delete("mykey")

        assert result is False


# delete_pattern


class TestDeletePattern:
    """Tests for CacheService.delete_pattern()."""

    @pytest.mark.asyncio
    async def test_matches_deletes_and_returns_count(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """When keys match the pattern, they should be deleted."""
        keys = {"test:user:1", "test:user:2", "test:user:3"}

        # Simulate scan_iter yielding matching keys.
        async def _scan_iter(**kwargs):  # noqa: ANN401
            for k in keys:
                yield k

        mock_redis.scan_iter = _scan_iter
        mock_redis.delete.return_value = 3

        result = await cache.delete_pattern("user:*")

        assert result == 3
        mock_redis.delete.assert_called_once()
        # The order of keys depends on set iteration, so compare as sorted lists.
        actual_args = sorted(mock_redis.delete.call_args[0])
        expected_args = sorted(keys)
        assert actual_args == expected_args

    @pytest.mark.asyncio
    async def test_no_matches_returns_zero(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """When no keys match, delete_pattern should return 0."""

        async def _scan_iter_empty(**kwargs):  # noqa: ANN401
            # Yield nothing.
            if False:  # pragma: no cover
                yield

        mock_redis.scan_iter = _scan_iter_empty

        result = await cache.delete_pattern("nobody:*")

        assert result == 0
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_error_returns_zero(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A scan ConnectionError should be caught and 0 returned."""

        async def _scan_iter_error(**kwargs):  # noqa: ANN401
            raise ConnectionError("scan failed")
            yield  # pragma: no cover

        mock_redis.scan_iter = _scan_iter_error

        result = await cache.delete_pattern("user:*")

        assert result == 0
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_error_after_scan_returns_zero(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """If scan succeeds but delete fails, return 0."""
        keys = {"test:user:1"}

        async def _scan_iter(**kwargs):  # noqa: ANN401
            for k in keys:
                yield k

        mock_redis.scan_iter = _scan_iter
        mock_redis.delete.side_effect = TimeoutError("delete timed out")

        result = await cache.delete_pattern("user:*")

        assert result == 0
        mock_redis.delete.assert_called_once_with(*keys)


# exists


class TestExists:
    """Tests for CacheService.exists()."""

    @pytest.mark.asyncio
    async def test_exists_returns_true(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """exists should return True when redis returns >= 1."""
        mock_redis.exists.return_value = 1

        result = await cache.exists("mykey")

        assert result is True
        mock_redis.exists.assert_called_once_with("test:mykey")

    @pytest.mark.asyncio
    async def test_exists_returns_false(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """exists should return False when redis returns 0."""
        mock_redis.exists.return_value = 0

        result = await cache.exists("nonexistent")

        assert result is False


# increment


class TestIncrement:
    """Tests for CacheService.increment()."""

    @pytest.mark.asyncio
    async def test_increment_default_amount(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """increment should call redis.incr with amount=1 by default."""
        mock_redis.incr.return_value = 42

        result = await cache.increment("counter")

        assert result == 42
        mock_redis.incr.assert_called_once_with("test:counter", 1)

    @pytest.mark.asyncio
    async def test_increment_custom_amount(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """increment should forward the custom amount to redis.incr."""
        mock_redis.incr.return_value = 10

        result = await cache.increment("counter", amount=5)

        assert result == 10
        mock_redis.incr.assert_called_once_with("test:counter", 5)


# get_ttl


class TestGetTtl:
    """Tests for CacheService.get_ttl()."""

    @pytest.mark.asyncio
    async def test_positive_ttl(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """get_ttl should return the remaining TTL in seconds."""
        mock_redis.ttl.return_value = 300

        result = await cache.get_ttl("mykey")

        assert result == 300
        mock_redis.ttl.assert_called_once_with("test:mykey")

    @pytest.mark.asyncio
    async def test_no_ttl_returns_minus_one(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """get_ttl should return -1 for a key with no TTL."""
        mock_redis.ttl.return_value = -1

        result = await cache.get_ttl("persistent")

        assert result == -1

    @pytest.mark.asyncio
    async def test_key_not_found_returns_minus_two(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """get_ttl should return -2 when the key does not exist."""
        mock_redis.ttl.return_value = -2

        result = await cache.get_ttl("ghost")

        assert result == -2


# Global functions (smoke tests)


class TestGlobalFunctions:
    """Smoke tests for module-level global helpers."""

    def test_get_redis_is_importable(self) -> None:
        """get_redis should be an async callable."""
        assert callable(get_redis)

    def test_close_redis_is_importable(self) -> None:
        """close_redis should be an async callable."""
        assert callable(close_redis)

    def test_get_cache_is_importable(self) -> None:
        """get_cache should be an async callable."""
        assert callable(get_cache)


# get_redis lifecycle (lifespan-based initialization)


class TestGetRedisLifecycle:
    """Tests for the lifespan-based Redis client lifecycle."""

    @pytest.mark.asyncio
    async def test_init_redis_creates_client(self) -> None:
        """init_redis should create a client via from_url and store it."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            mock_client = MagicMock()
            with patch(
                "redis.asyncio.Redis.from_url", return_value=mock_client
            ) as mock_from_url:
                await cache_module.init_redis()

            mock_from_url.assert_called_once()
            assert cache_module._redis_client is mock_client
        finally:
            cache_module._redis_client = original

    @pytest.mark.asyncio
    async def test_get_redis_returns_after_init(self) -> None:
        """get_redis should return the client after init_redis has been called."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            mock_client = MagicMock()
            with patch("redis.asyncio.Redis.from_url", return_value=mock_client):
                await cache_module.init_redis()

            result = await get_redis()
            assert result is mock_client
        finally:
            cache_module._redis_client = original

    @pytest.mark.asyncio
    async def test_get_redis_raises_before_init(self) -> None:
        """get_redis should raise RuntimeError if called before init_redis."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            with pytest.raises(RuntimeError, match="not initialized"):
                await get_redis()
        finally:
            cache_module._redis_client = original

    @pytest.mark.asyncio
    async def test_init_redis_raises_when_already_initialized(self) -> None:
        """init_redis should raise if the client is already initialized."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            mock_client = MagicMock()
            with patch("redis.asyncio.Redis.from_url", return_value=mock_client):
                await cache_module.init_redis()

            with pytest.raises(RuntimeError, match="already initialized"):
                await cache_module.init_redis()
        finally:
            cache_module._redis_client = original

    @pytest.mark.asyncio
    async def test_second_call_returns_same_instance(self) -> None:
        """Multiple calls to get_redis should return the same client instance."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            mock_client = MagicMock()
            with patch("redis.asyncio.Redis.from_url", return_value=mock_client):
                await cache_module.init_redis()

            first = await get_redis()
            second = await get_redis()
            assert first is second
            assert first is mock_client
        finally:
            cache_module._redis_client = original

    @pytest.mark.asyncio
    async def test_init_redis_cluster_mode(self) -> None:
        """When REDIS_CLUSTER is True, init_redis should create a RedisCluster."""
        import app.core.cache as cache_module

        original = cache_module._redis_client
        try:
            cache_module._redis_client = None

            mock_cluster = MagicMock()
            with (
                patch.object(cache_module.settings, "REDIS_CLUSTER", True),
                patch(
                    "redis.asyncio.cluster.RedisCluster", return_value=mock_cluster
                ) as mock_cluster_init,
            ):
                await cache_module.init_redis()

            mock_cluster_init.assert_called_once()
            assert cache_module._redis_client is mock_cluster
        finally:
            cache_module._redis_client = original


# close_redis ordering


class TestCloseRedis:
    """Tests for close_redis() ordering and state cleanup."""

    @pytest.mark.asyncio
    async def test_closes_before_setting_none(self) -> None:
        """close_redis should call client.close() BEFORE setting _redis_client to None."""
        import app.core.cache as cache_module

        original_client = cache_module._redis_client
        original_cache = cache_module._cache
        try:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            cache_module._redis_client = mock_client

            await close_redis()

            mock_client.close.assert_awaited_once()
            assert cache_module._redis_client is None
            assert cache_module._cache is None
        finally:
            cache_module._redis_client = original_client
            cache_module._cache = original_cache


# get_cache instance reuse


class TestGetCacheReuse:
    """Tests for get_cache() instance reuse."""

    @pytest.mark.asyncio
    async def test_reuses_existing_cache_instance(self) -> None:
        """get_cache should return the existing _cache instance if already set."""
        import app.core.cache as cache_module

        original_client = cache_module._redis_client
        original_cache = cache_module._cache
        try:
            mock_redis_client = MagicMock()
            cache_module._redis_client = mock_redis_client

            existing = CacheService(mock_redis_client, prefix="test")
            cache_module._cache = existing

            result = await get_cache()
            assert result is existing
        finally:
            cache_module._redis_client = original_client
            cache_module._cache = original_cache


class TestCachedNoneIsAValue:
    """`None` is a result, not an absence.

    `get_or_set` tested its read against `None`, which `get()` also
    returns for a missing key. A `compute()` that legitimately returns
    `None`, a lookup that found nothing, was therefore stored and then
    read back as a miss forever: recomputed on every call, never served.
    The waiters had it worse, polling for a value that by construction
    never appears, so each burned the full `wait_timeout` before
    computing anyway. The stampede protection guaranteed a stampede.
    """

    @pytest.mark.asyncio
    async def test_a_cached_none_is_served_without_recomputing(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """A stored `null` must count as a hit.

        Args:
            cache: Cache service under test.
            mock_redis: Mocked Redis client.
        """
        mock_redis.get.return_value = "null"
        compute = AsyncMock(return_value="should not run")

        result = await cache.get_or_set("mykey", compute)

        assert result is None
        compute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_waiter_accepts_a_none_published_by_the_winner(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """The polling loop must stop on `null` too.

        Args:
            cache: Cache service under test.
            mock_redis: Mocked Redis client.
        """
        # Missing on the first read, then the winner publishes `null`.
        mock_redis.get.side_effect = [None, "null"]
        # Lost the race for the lock.
        mock_redis.set.return_value = None
        compute = AsyncMock(return_value="should not run")

        result = await cache.get_or_set(
            "mykey", compute, wait_timeout=1.0, retry_interval=0.01
        )

        assert result is None
        compute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_truly_absent_key_still_computes(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """Distinguishing the two must not break the ordinary miss.

        Args:
            cache: Cache service under test.
            mock_redis: Mocked Redis client.
        """
        mock_redis.get.return_value = None
        mock_redis.set.return_value = True
        compute = AsyncMock(return_value={"computed": True})

        result = await cache.get_or_set("mykey", compute)

        assert result == {"computed": True}
        compute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_still_reports_none_for_both(
        self, cache: CacheService, mock_redis: AsyncMock
    ) -> None:
        """`get()`'s own contract is unchanged.

        Args:
            cache: Cache service under test.
            mock_redis: Mocked Redis client.
        """
        mock_redis.get.return_value = None
        assert await cache.get("absent") is None

        mock_redis.get.return_value = "null"
        assert await cache.get("cached-null") is None
