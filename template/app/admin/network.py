"""Keeping the administration surface off the open internet.

A request from outside `ADMIN_ALLOWED_NETWORKS` gets the same 404 as a
path that does not exist, so the sign-in form is not there to find, let
alone to guess passwords against.

The client address is the one `uvicorn` resolved. It honours
`X-Forwarded-For` only from the peers in `FORWARDED_ALLOW_IPS`, so behind
a proxy whose address that list misses, every request looks like it came
from the proxy's own private address. Every address the header names has
to be allowed too, so that mistake shuts the surface rather than opening
it to the internet.
"""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Network
from ipaddress import IPv6Address
from ipaddress import IPv6Network
from ipaddress import ip_address

from starlette.responses import JSONResponse
from starlette.types import ASGIApp
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from app.core.security_middleware import ADMIN_PATH_PREFIX


def _is_administration(path: str) -> bool:
    """Whether `path` belongs to the surface, its stylesheet included."""
    return path == ADMIN_PATH_PREFIX or path.startswith(f"{ADMIN_PATH_PREFIX}/")


def _forwarded(scope: Scope) -> list[str]:
    """Every address the `X-Forwarded-For` headers name, in order."""
    return [
        hop.strip()
        for name, value in scope.get("headers", [])
        if name == b"x-forwarded-for"
        for hop in value.decode("latin-1").split(",")
    ]


class AdminNetworkMiddleware:
    """Answer the administration surface only inside the allowed networks."""

    def __init__(
        self,
        app: ASGIApp,
        networks: Sequence[IPv4Network | IPv6Network],
    ) -> None:
        self.app = app
        self.networks = tuple(networks)

    def allows(self, host: str | None) -> bool:
        """Whether a client at `host` may reach the surface.

        Anything that is not an address, a missing client included, is
        refused rather than guessed at.
        """
        if not host:
            return False
        try:
            address = ip_address(host)
        except ValueError:
            return False
        # An IPv4 client on a dual-stack socket arrives as `::ffff:a.b.c.d`,
        # which no IPv4 network contains.
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.networks)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse with a plain 404, or pass the request on."""
        if scope["type"] == "http" and _is_administration(scope["path"]):
            client = scope.get("client")
            hops = [client[0] if client else None, *_forwarded(scope)]
            if not all(self.allows(hop) for hop in hops):
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
