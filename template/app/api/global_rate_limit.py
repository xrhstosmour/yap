"""A ceiling on how much one caller can ask of the whole API.

The per-user limiter in `app.dependencies` only runs once a route has
authenticated someone, and the auth limiter only guards a few routes, so
everything else answered anybody, as fast as they could send. This sits in
front of every route and does two things:

- Sliding window per caller: a valid, not revoked access token is keyed by
  its user, anything else by client IP. Each has its own allowance. An
  `X-API-Key` is counted under a hash of the whole header value as well as
  under the client IP, never instead of it. The key is not verified here
  (this runs before authentication), so a made-up one costs nothing and a
  fresh one per request would otherwise be a fresh allowance. The key bucket
  can only add a limit, the IP one always applies, and a made-up key cannot
  spend or block the real key's bucket. IPv6 clients are keyed by their /64,
  the smallest block one subscriber usually holds, or the allowance would be
  one per address.
- A block for callers that probe: one that collects too many 404, 405 and
  422 answers in a minute is refused outright for a while, counted under the
  same keys as its allowance, so a key caller is also counted, and blocked,
  by IP. 401, 403 and 429 are not counted, honest clients meet those.

Both keep their counters in Redis under their own key prefixes, so they
never add to the per-user count in `app.dependencies`. Both fail open when
Redis is unreachable or slow, as the other limiters do. Every Redis step is
bounded by `REDIS_TIMEOUT_SECONDS`. A probing answer is counted once its
response has been sent, so the caller does not wait for it, the connection
is held for at most that long.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from typing import TYPE_CHECKING
from typing import NamedTuple

import jwt
from fastapi.responses import JSONResponse
from redis.exceptions import RedisClusterException
from redis.exceptions import RedisError
from starlette.datastructures import Headers

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.security import ALGORITHM
from app.core.security import is_token_blacklisted
from app.core.settings import settings

if TYPE_CHECKING:
    from starlette.types import ASGIApp
    from starlette.types import Message
    from starlette.types import Receive
    from starlette.types import Scope
    from starlette.types import Send

logger = get_logger("api.global_rate_limit")

#: Probes and the like, never limited or counted.
EXEMPT_PATHS = frozenset(
    {
        f"{settings.API_V1_STR}/live",
        f"{settings.API_V1_STR}/ready",
        f"{settings.API_V1_STR}/health",
        "/admin",
    }
)

#: The administration surface and its stylesheet.
EXEMPT_PREFIXES = ("/admin/",)

#: Answers that count as probing.
FAILURE_STATUSES = frozenset({404, 405, 422})

RATE_LIMITED = "Rate limit exceeded"

#: How long a Redis step may take before it is given up on, failing open.
REDIS_TIMEOUT_SECONDS = 1

#: Everything Redis, or its cluster client, raises when it cannot answer.
REDIS_ERRORS = (RedisError, RedisClusterException, RuntimeError)

#: Counts a probing answer and sets the block in one step, so a crash or a
#: concurrent request cannot leave a count without its expiry or a threshold
#: crossed without its block. Both keys carry the same `{hash tag}`, see
#: `_keys`, or a Redis Cluster refuses a script over keys in two slots.
COUNT_FAILURE_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if redis.call("TTL", KEYS[1]) < 0 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
if count >= tonumber(ARGV[2]) then
    redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
    return 1
end
return 0
"""


class Caller(NamedTuple):
    """Who a request is counted as: a kind and an identifier within it."""

    scope: str
    identifier: str


def normalize_client_address(host: str | None) -> str:
    """Return the form of a client address that limits are counted under.

    An IPv4 address a dual-stack listener reports as `::ffff:a.b.c.d` is
    the same client as `a.b.c.d`, and would otherwise get a second allowance.
    Any other IPv6 address stands for its /64, since one subscriber holds a
    whole block and could otherwise change address for a fresh allowance on
    every request.

    Args:
        host: The address from the ASGI scope, if any.

    Returns:
        The address, with an IPv4-mapped IPv6 one written as IPv4 and any
        other IPv6 one as its /64 network, or `unknown` when there is none.
    """
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ipaddress.IPv6Network((address, 64), strict=False))
    except ValueError:
        return host
    return str(address)


def _api_key_identifier(headers: Headers) -> str | None:
    """Name the bucket of an `X-API-Key` by a hash of the whole header value.

    Nothing is looked up or verified, see the module note, so the bucket
    only adds a limit on top of the IP one. Hashing the secret too means that
    knowing a key's ID is not enough to land in its bucket, so a forged
    secret cannot spend its allowance or get it blocked. The value is hashed
    so it never reaches Redis or the logs as it is.

    Args:
        headers: Request headers.

    Returns:
        A short hash of the header value, or `None` when there is no header
        or it does not look like `key_id:secret`.
    """
    value = headers.get("x-api-key", "")
    key_id, separator, secret = value.partition(":")
    if not key_id or not separator or not secret:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _authenticated_user(headers: Headers) -> tuple[str, str | None] | None:
    """Read the user from a valid, unexpired access token, without any lookup.

    Only the signature, expiry and type are checked here, revocation is
    looked up by the caller from the `jti`. The user may still have been
    deactivated, that is for the route's own authentication. An API key
    never counts, it is not a signed token.

    The token is decoded raw, not with `decode_token`. That also compares
    the token's database claim with the database this request is routed to,
    which `DatabaseModeMiddleware` sets further in. Out here it is never
    set, so a token for the additional database would be rejected.

    Args:
        headers: Request headers.

    Returns:
        The token's `sub` and `jti`, or `None` for anything else.
    """
    authorization = headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str) or not subject:
        return None
    identifier = payload.get("jti")
    return subject, identifier if isinstance(identifier, str) else None


def _keys(caller: Caller) -> tuple[str, str]:
    """The failure count and block keys of a caller.

    Both share the `{scope:identifier}` hash tag, so they live in one slot
    on a Redis Cluster and one script can use both.
    """
    tag = f"{{{caller.scope}:{caller.identifier}}}"
    return f"abuse:{tag}:failures", f"abuse:{tag}:block"


class GlobalRateLimitMiddleware:
    """Answer 429 to callers over their allowance or blocked for probing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limiters = {
            "user": RateLimiter(
                requests_per_minute=settings.RATE_LIMIT_GLOBAL_AUTHENTICATED_PER_MINUTE,
                key_prefix="ratelimit:global:user",
            ),
            "key": RateLimiter(
                requests_per_minute=settings.RATE_LIMIT_GLOBAL_API_KEY_PER_MINUTE,
                key_prefix="ratelimit:global:key",
            ),
            "ip": RateLimiter(
                requests_per_minute=settings.RATE_LIMIT_GLOBAL_ANONYMOUS_PER_MINUTE,
                key_prefix="ratelimit:global:ip",
            ),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.RATE_LIMIT_GLOBAL_ENABLED:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if (
            scope["method"] == "OPTIONS"
            or path in EXEMPT_PATHS
            or path.startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        address = self._address(scope)
        callers, token_identifier = self._callers(scope, address)

        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                # A revoked token must not spend, or block, its user's bucket.
                if token_identifier is not None and await is_token_blacklisted(
                    token_identifier
                ):
                    callers = [address]
                # Every block and allowance is an independent key, asked
                # together so they cost one round trip of latency, not one
                # per key. Every allowance is charged, even when another one
                # refuses, so the IP is counted for every request.
                blocks, results = await asyncio.gather(
                    asyncio.gather(*(self._remaining_block(c) for c in callers)),
                    asyncio.gather(
                        *(
                            self.limiters[c.scope].check_rate_limit(c.identifier)
                            for c in callers
                        )
                    ),
                )
        except (TimeoutError, *REDIS_ERRORS):
            logger.warning("global_rate_limit_unavailable", exc_info=True)
            blocks, results = [0], [(True, 0, 0)]
        remaining_block = max(blocks)
        if remaining_block > 0:
            await self._refuse(scope, receive, send, remaining_block)
            return
        # Come back only once every refusing allowance has room again.
        waits = []
        for caller, (allowed, _, retry_after) in zip(callers, results, strict=False):
            if not allowed:
                logger.warning(
                    "global_rate_limit_exceeded",
                    caller_scope=caller.scope,
                    caller=caller.identifier,
                )
                waits.append(retry_after)
        if waits:
            await self._refuse(scope, receive, send, max(waits))
            return

        failed = False

        async def counting_send(message: Message) -> None:
            nonlocal failed
            if message["type"] == "http.response.start":
                failed = message["status"] in FAILURE_STATUSES
            await send(message)
            # Counted once the last byte is out. The caller already has the
            # whole response, only the connection is held until Redis answers
            # or `REDIS_TIMEOUT_SECONDS` passes.
            if (
                failed
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                failed = False
                await asyncio.gather(*(self._count_failure(c) for c in callers))

        await self.app(scope, receive, counting_send)

    @staticmethod
    def _address(scope: Scope) -> Caller:
        """The caller as its client address, which is also the fallback."""
        client = scope.get("client")
        return Caller("ip", normalize_client_address(client[0] if client else None))

    @staticmethod
    def _callers(scope: Scope, address: Caller) -> tuple[list[Caller], str | None]:
        """Pick who a request is counted as: its user, or its IP.

        An API key is unverified here, so it never replaces the IP, it is
        counted next to it. A valid token replaces it, it is signed.

        Returns:
            The callers, and the `jti` to check for revocation when the
            caller is a user.
        """
        headers = Headers(scope=scope)
        user = _authenticated_user(headers)
        if user is not None:
            return [Caller("user", user[0])], user[1]
        key_identifier = _api_key_identifier(headers)
        if key_identifier is not None:
            return [Caller("key", key_identifier), address], None
        return [address], None

    @staticmethod
    async def _remaining_block(caller: Caller) -> int:
        """Seconds left on a caller's block, 0 when it is not blocked."""
        redis = await get_redis()
        remaining = await redis.ttl(_keys(caller)[1])
        return max(int(remaining), 0)

    @staticmethod
    async def _count_failure(caller: Caller) -> None:
        """Count a probing answer, and block the caller at the threshold."""
        failures_key, block_key = _keys(caller)
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                redis = await get_redis()
                blocked = await redis.eval(  # type: ignore[misc]
                    COUNT_FAILURE_SCRIPT,
                    2,
                    failures_key,
                    block_key,
                    str(settings.ABUSE_FAILURE_WINDOW_SECONDS),
                    str(settings.ABUSE_FAILURE_THRESHOLD),
                    str(settings.ABUSE_BLOCK_SECONDS),
                )
        except (TimeoutError, *REDIS_ERRORS):
            logger.warning("abuse_failure_count_unavailable", exc_info=True)
            return
        if blocked:
            logger.warning(
                "abuse_caller_blocked",
                caller_scope=caller.scope,
                caller=caller.identifier,
            )

    @staticmethod
    async def _refuse(
        scope: Scope, receive: Receive, send: Send, retry_after: int
    ) -> None:
        response = JSONResponse(
            status_code=429,
            content={"detail": RATE_LIMITED},
            headers={"Retry-After": str(max(retry_after, 1))},
        )
        await response(scope, receive, send)
