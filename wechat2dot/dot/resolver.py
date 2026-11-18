"""Provide public DoH answers to aiohttp without changing TLS hostname verification."""

import asyncio
import socket
from aiohttp.abc import AbstractResolver
from wechat_dot_bridge.net_transport import NetworkFailure, resolve_addresses


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        try:
            rows = await asyncio.to_thread(resolve_addresses, host, dns="google")
        except NetworkFailure as exc:
            raise OSError("public DNS unavailable") from exc
        return [
            dict(
                hostname=host,
                host=r[3][0],
                port=port,
                family=r[0],
                proto=r[2],
                flags=socket.AI_NUMERICHOST,
            )
            for r in rows
        ]

    async def close(self):
        pass
