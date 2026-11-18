"""Small aiohttp wrapper with bounded bodies and fixed-host file downloads."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024


class NetworkError(Exception):
    """Nothing reached the server, or the connection broke before a response."""


class NotConnected(NetworkError):
    """DNS, proxy connection, or TLS setup failed before the request could be written."""


class FileUnavailable(Exception):
    """The server confirmed that this particular attachment cannot be retrieved."""


class FileTooLarge(Exception):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    body: object
    headers: dict


@dataclass(frozen=True)
class Upload:
    field: str
    name: str
    mime: str
    data: bytes


async def read_bounded(stream, limit: int, too_large) -> bytes:
    """StreamReader.read(n) returns what is buffered, not n bytes; read to EOF instead."""
    chunks, size = [], 0
    async for chunk in stream.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


class Http:
    def __init__(self, session=None, dns="system"):
        self.dns = dns
        self._session = session

    async def _client(self):
        if self._session is None or self._session.closed:
            import aiohttp
            from .resolver import PublicResolver

            connector = (
                aiohttp.TCPConnector(resolver=PublicResolver())
                if self.dns == "google"
                else None
            )
            self._session = aiohttp.ClientSession(trust_env=True, connector=connector)
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(
        self,
        method,
        url,
        *,
        headers=None,
        json_body=None,
        form=None,
        params=None,
        timeout=30,
        upload: Upload | None = None,
    ) -> Response:
        import aiohttp

        session = await self._client()
        options = {
            "headers": headers or {},
            "params": params,
            "timeout": aiohttp.ClientTimeout(total=timeout),
            "allow_redirects": False,
        }
        if upload is not None:
            data = aiohttp.FormData()
            data.add_field(
                upload.field,
                upload.data,
                filename=upload.name,
                content_type=upload.mime,
            )
            options["data"] = data
        elif form is not None:
            options["data"] = form
        elif json_body is not None:
            options["json"] = json_body
        try:
            async with session.request(method, url, **options) as resp:
                raw = await read_bounded(
                    resp.content, MAX_BODY_BYTES, NetworkError("response too large")
                )
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                return Response(resp.status, body, dict(resp.headers))
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError) as exc:
            raise NotConnected(type(exc).__name__) from None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise NetworkError(type(exc).__name__) from None

    async def fetch(self, url, *, host_ok, timeout=120) -> bytes:
        """GET a signed file URL without account headers; only https on an allowed host."""
        import aiohttp

        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or not host_ok(parts.hostname)
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
        ):
            raise NetworkError("file host not allowed")
        session = await self._client()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False
            ) as resp:
                if resp.status in (400, 404, 410):
                    raise FileUnavailable()
                if resp.status != 200:
                    raise NetworkError(f"file HTTP {resp.status}")
                return await read_bounded(resp.content, MAX_FILE_BYTES, FileTooLarge())
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise NetworkError(type(exc).__name__) from None
