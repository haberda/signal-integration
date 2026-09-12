"""Small asynchronous client; no Home Assistant dependencies or payload logging."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

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


def validate_link_uri(value: str) -> str:
    """Accept Signal provisioning URIs without reflecting secret input in errors."""
    try:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError
        uri = urlsplit(value.strip())
        if uri.scheme not in {"sgnl", "tsdevice"}:
            raise ValueError
        if uri.scheme == "sgnl" and uri.netloc != "linkdevice":
            raise ValueError
        query = parse_qs(uri.query)
        if not query.get("uuid") or not query.get("pub_key"):
            raise ValueError
    except ValueError, TypeError:
        raise ValueError("Invalid Signal device link URI") from None
    return value.strip()


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
        if not isinstance(about.get("versions"), list) or "v2" not in about["versions"]:
            raise UnsupportedServer("The API must support v2 sending")
        accounts = await self.accounts()
        self.mode = about["mode"]
        return about, accounts

    async def accounts(self) -> list[str]:
        """List backend accounts without starting a linking operation."""
        accounts = await self.request("GET", "v1/accounts", request_timeout=30)
        if not isinstance(accounts, list) or not all(
            isinstance(a, str) and a for a in accounts
        ):
            raise InvalidResponse("Invalid account list")
        return list(dict.fromkeys(accounts))

    async def start_link(self, device_name: str) -> str:
        """Start one backend-owned link handshake; never retry implicitly."""
        async with self.lock:
            result = await self.request(
                "GET",
                "v1/qrcodelink/raw",
                params={"device_name": device_name},
                request_timeout=30,
            )
        try:
            return validate_link_uri(result["device_link_uri"])
        except KeyError, TypeError, ValueError:
            raise InvalidResponse("Invalid device link response") from None

    async def devices(self, account: str) -> list[dict]:
        async with self.lock:
            result = await self.request("GET", f"v1/devices/{quote(account, safe='')}")
        if not isinstance(result, list) or any(
            not isinstance(device, dict)
            or type(device.get("id")) is not int
            or device["id"] < 1
            or not isinstance(device.get("name", ""), str)
            for device in result
        ):
            raise InvalidResponse("Invalid device list")
        return result

    async def add_device(self, account: str, uri: str) -> None:
        uri = validate_link_uri(uri)
        async with self.lock:
            await self.request(
                "POST", f"v1/devices/{quote(account, safe='')}", data={"uri": uri}
            )

    async def remove_device(self, account: str, device_id: int) -> None:
        if type(device_id) is not int or device_id <= 1:
            raise ValueError("Select a linked device, not the primary device")
        async with self.lock:
            await self.request(
                "DELETE", f"v1/devices/{quote(account, safe='')}/{device_id}"
            )

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
            async with asyncio.timeout(15):
                ws = await self.session.ws_connect(
                    url,
                    headers=self.headers,
                    ssl=self.ssl,
                    autoping=True,
                    heartbeat=30,
                    max_msg_size=MAX_RESPONSE_BYTES,
                )
            async with ws:
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

    async def react(
        self,
        account: str,
        recipient: str,
        target_author: str,
        timestamp: int,
        emoji: str = "",
        *,
        remove: bool = False,
    ) -> None:
        """Send/remove a reaction without retrying uncertain requests."""
        async with self.lock:
            await self.request(
                "DELETE" if remove else "POST",
                f"v1/reactions/{quote(account, safe='')}",
                data={
                    "recipient": recipient,
                    "target_author": target_author,
                    "timestamp": timestamp,
                    "reaction": emoji,
                },
            )

    async def groups(self, account: str) -> list[dict]:
        """Return current group metadata for UI management."""
        async with self.lock:
            result = await self.request("GET", f"/v1/groups/{quote(account, safe='')}")
        if not isinstance(result, list) or any(
            not isinstance(group, dict)
            or not isinstance(group.get("id"), str)
            or not group["id"].startswith("group.")
            for group in result
        ):
            raise InvalidResponse("Invalid groups response")
        return result

    async def change_group(
        self, account: str, action: str, group_id: str | None, data: dict
    ) -> str | None:
        """Perform one operation and return the new group ID for creation."""
        routes = {
            "create": ("POST", ""),
            "edit": ("PUT", ""),
            "add_members": ("POST", "/members"),
            "remove_members": ("DELETE", "/members"),
            "add_admins": ("POST", "/admins"),
            "remove_admins": ("DELETE", "/admins"),
            "leave": ("POST", "/quit"),
        }
        method, suffix = routes[action]
        path = f"/v1/groups/{quote(account, safe='')}"
        if action != "create":
            if not group_id or not group_id.startswith("group."):
                raise ValueError("A group ID is required")
            path += f"/{quote(group_id, safe='')}"
        async with self.lock:
            result = await self.request(method, path + suffix, data=data)
        if action == "create" and (
            not isinstance(result, dict)
            or not isinstance(result.get("id"), str)
            or not result["id"].startswith("group.")
        ):
            raise InvalidResponse("Invalid create group response")
        return result["id"] if action == "create" else None
