"""Small asynchronous client; no Home Assistant dependencies or payload logging."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import MAX_RESPONSE_BYTES, MODES


class SignalError(Exception):
    """Sanitized backend failure."""


class CannotConnect(SignalError):
    """Transport failed; sends must not be automatically retried."""


class InvalidAuth(SignalError):
    """Proxy rejected credentials."""


class InvalidResponse(SignalError):
    """The server did not return the expected contract."""


class UnsupportedServer(SignalError):
    """The backend does not implement the supported contract."""


def normalize_url(value: str) -> str:
    """Canonicalize the base URL while retaining reverse-proxy paths."""
    try:
        url = URL(value.strip())
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.user is not None
            or url.query_string
            or url.fragment
        ):
            raise ValueError
        _ = url.port
    except (ValueError, TypeError) as err:
        raise ValueError(
            "Enter an HTTP(S) URL without credentials, query or fragment"
        ) from err
    return str(url).rstrip("/")


class SignalClient:
    """Use an injected session, including for authenticated WebSockets."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str = "",
        password: str = "",
        verify_ssl: bool = True,
    ):
        self.session = session
        self.url = normalize_url(url)
        self.headers = (
            {"Authorization": aiohttp.encode_basic_auth(username, password)}
            if username
            else {}
        )
        self.ssl = verify_ssl
        self.lock = asyncio.Lock()
        self.mode = "json-rpc"

    def endpoint(self, path: str) -> str:
        return f"{self.url}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict | None = None,
        params: dict | None = None,
        request_timeout: int = 60,
    ) -> Any:
        """Bound response memory and never surface backend error bodies."""
        try:
            async with self.session.request(
                method,
                self.endpoint(path),
                json=data,
                params=params,
                headers=self.headers,
                ssl=self.ssl,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=request_timeout),
            ) as response:
                if response.status in (401, 403):
                    raise InvalidAuth("API authentication failed")
                if not 200 <= response.status < 300:
                    raise SignalError(f"API request failed (HTTP {response.status})")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise InvalidResponse("API response exceeded the size limit")
                if not body:
                    return None
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError) as err:
                    raise InvalidResponse("API returned invalid JSON") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(
                "Cannot reach API; a send may already have succeeded"
            ) from err

    async def discover(self) -> tuple[dict, list[str]]:
        about = await self.request("GET", "v1/about", request_timeout=15)
        if not isinstance(about, dict) or about.get("mode") not in MODES:
            raise UnsupportedServer("Unsupported or missing execution mode")
        if "v2" not in about.get("versions", []):
            raise UnsupportedServer("The API must support v2 sending")
        accounts = await self.request("GET", "v1/accounts", request_timeout=30)
        if not isinstance(accounts, list) or not all(
            isinstance(a, str) for a in accounts
        ):
            raise InvalidResponse("Invalid account list")
        self.mode = about["mode"]
        return about, accounts

    async def destinations(self, account: str) -> dict[str, str]:
        """Metadata is optional; callers may always enter destinations manually."""
        choices: dict[str, str] = {}
        number = quote(account, safe="")
        for resource in ("contacts", "groups"):
            try:
                async with self.lock:
                    items = await self.request(
                        "GET", f"v1/{resource}/{number}", request_timeout=30
                    )
            except InvalidAuth:
                raise
            except SignalError:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                recipient = (
                    item.get("id")
                    if resource == "groups"
                    else item.get("number") or item.get("uuid")
                )
                if not isinstance(recipient, str) or not recipient:
                    continue
                label = item.get("name") or item.get("profile_name") or recipient
                choices[recipient] = f"{label} ({recipient})"
        return choices

    async def send(
        self, account: str, recipients: list[str], message: str, **fields: Any
    ) -> dict:
        # Serialize account operations, including short polling receives.
        async with self.lock:
            result = await self.request(
                "POST",
                "v2/send",
                data={
                    "number": account,
                    "recipients": recipients,
                    "message": message,
                    "notify_self": True,
                    **fields,
                },
            )
        if not isinstance(result, dict) or "timestamp" not in result:
            raise InvalidResponse(
                "Send returned no message reference; delivery is uncertain"
            )
        return result

    async def receive(self, account: str) -> list:
        async with self.lock:
            result = await self.request(
                "GET",
                f"v1/receive/{quote(account, safe='')}",
                params={
                    "timeout": "1",
                    "max_messages": "100",
                    "ignore_attachments": "true",
                    "ignore_stories": "true",
                    "ignore_avatars": "true",
                    "ignore_stickers": "true",
                    "send_read_receipts": "false",
                },
            )
        if not isinstance(result, list):
            raise InvalidResponse("Invalid receive batch")
        return result

    async def messages(self, account: str) -> AsyncIterator[dict]:
        url = URL(self.endpoint(f"v1/receive/{quote(account, safe='')}"))
        url = url.with_scheme("wss" if url.scheme == "https" else "ws")
        try:
            async with self.session.ws_connect(
                url,
                headers=self.headers,
                ssl=self.ssl,
                autoping=True,
                heartbeat=30,
                max_msg_size=MAX_RESPONSE_BYTES,
            ) as ws:
                # The caller learns the handshake succeeded before any message arrives.
                yield {"_connected": True}
                async for frame in ws:
                    if frame.type == aiohttp.WSMsgType.TEXT:
                        try:
                            value = json.loads(frame.data)
                        except ValueError:
                            continue
                        if isinstance(value, dict):
                            if "error" in value:
                                raise SignalError("Receive stream reported an error")
                            yield value
                    elif frame.type == aiohttp.WSMsgType.ERROR:
                        raise CannotConnect("Receive stream failed")
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise InvalidAuth("API authentication failed") from err
            raise CannotConnect("WebSocket handshake failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect("Receive stream disconnected") from err
