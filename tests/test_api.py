"""Exercise the actual HTTP and WebSocket client against a local server."""

import aiohttp
import pytest
from aiohttp import web

from custom_components.signal_messenger_rest.api import (
    CannotConnect,
    InvalidAuth,
    InvalidResponse,
    SignalClient,
    SignalError,
    normalize_url,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "http://user:pass@host",
        "http://host?q=x",
        "http://host/#x",
        "host",
        "http://host:bad",
    ],
)
def test_bad_url(url):
    with pytest.raises(ValueError):
        normalize_url(url)


async def test_http_contract(aiohttp_server):
    seen = []

    async def handler(request):
        seen.append(request)
        if request.path.endswith("about"):
            return web.json_response({"mode": "native", "versions": ["v2"]})
        if request.path.endswith("accounts"):
            return web.json_response(["+12025550100"])
        if request.method == "POST":
            body = await request.json()
            assert body["quote_timestamp"] == 123
            assert body["notify_self"] is True
            assert body["recipients"] == ["group.test"]
            return web.json_response({"timestamp": "456", "errors": {"recipients": []}})
        assert request.query["send_read_receipts"] == "false"
        return web.json_response([])

    app = web.Application()
    app.router.add_route("*", "/proxy/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(
            session, str(server.make_url("/proxy/")), "name", "secret"
        )
        about, accounts = await client.discover()
        assert client.mode == "native"
        assert accounts == ["+12025550100"]
        assert (
            await client.send(accounts[0], ["group.test"], "hello", quote_timestamp=123)
        )["timestamp"] == "456"
        assert await client.receive(accounts[0]) == []
    assert all(
        r.headers["Authorization"] == aiohttp.encode_basic_auth("name", "secret")
        for r in seen
    )


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, "secret", InvalidAuth),
        (500, "private message", SignalError),
        (200, "not json", InvalidResponse),
        (302, "", SignalError),
    ],
)
async def test_sanitized_errors(aiohttp_server, status, body, error):
    async def handler(request):
        return web.Response(status=status, text=body)

    app = web.Application()
    app.router.add_get("/v1/about", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        with pytest.raises(error) as exc:
            await client.request("GET", "v1/about")
        assert body not in str(exc.value) if body else True


async def test_websocket(aiohttp_server):
    async def handler(request):
        assert request.headers["Authorization"] == aiohttp.encode_basic_auth(
            "name", "secret"
        )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("invalid")
        await ws.send_json({"envelope": {"timestamp": 123}})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/prefix/v1/receive/{account}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(
            session, str(server.make_url("/prefix")), "name", "secret"
        )
        messages = [m async for m in client.messages("+12025550100")]
        assert messages == [{"_connected": True}, {"envelope": {"timestamp": 123}}]


async def test_send_not_retried(aiohttp_server):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        request.transport.close()
        return web.Response()

    app = web.Application()
    app.router.add_post("/v2/send", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        with pytest.raises(CannotConnect):
            await client.send("a", ["b"], "private")
    assert count == 1


async def test_destination_discovery_and_optional_failure(aiohttp_server):
    async def contacts(request):
        return web.json_response(
            [
                {"number": "+12025550101", "name": "Alice"},
                {"uuid": "sender-uuid", "profile_name": "Private number"},
                {"name": "no address"},
                "bad",
            ]
        )

    async def groups(request):
        return web.json_response([{"id": "group.test", "name": "Household"}])

    app = web.Application()
    app.router.add_get("/v1/contacts/{account}", contacts)
    app.router.add_get("/v1/groups/{account}", groups)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        choices = await client.destinations("+12025550100")
        assert choices == {
            "+12025550101": "Alice (+12025550101)",
            "sender-uuid": "Private number (sender-uuid)",
            "group.test": "Household (group.test)",
        }


@pytest.mark.parametrize("status", [404, 401])
async def test_missing_metadata_or_auth(aiohttp_server, status):
    async def handler(request):
        return web.Response(status=status)

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        if status == 401:
            with pytest.raises(InvalidAuth):
                await client.destinations("account")
        else:
            assert await client.destinations("account") == {}


async def test_websocket_auth(aiohttp_server):
    async def handler(request):
        return web.Response(status=401)

    app = web.Application()
    app.router.add_get("/v1/receive/{account}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        with pytest.raises(InvalidAuth):
            async for _ in client.messages("account"):
                pass


async def test_group_http_contract(aiohttp_server):
    seen = []

    async def handler(request):
        seen.append(
            (
                request.method,
                request.path,
                await request.json() if request.can_read_body else None,
            )
        )
        if request.method == "GET":
            return web.json_response([{"id": "group.test", "name": "Home"}])
        if request.path.endswith("+123"):
            return web.json_response({"id": "group.new"}, status=201)
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/proxy/v1/groups/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/proxy")))
        assert (await client.groups("+123"))[0]["name"] == "Home"
        await client.change_group(
            "+123", "create", None, {"name": "New", "members": ["+456"]}
        )
        await client.change_group(
            "+123", "remove_members", "group.test", {"members": ["+456"]}
        )
    assert seen == [
        ("GET", "/proxy/v1/groups/+123", None),
        ("POST", "/proxy/v1/groups/+123", {"name": "New", "members": ["+456"]}),
        ("DELETE", "/proxy/v1/groups/+123/group.test/members", {"members": ["+456"]}),
    ]


@pytest.mark.parametrize("status", [204, 400, 401])
async def test_read_receipt_contract(aiohttp_server, status):
    seen = []

    async def handler(request):
        seen.append(await request.json())
        assert request.path == "/proxy/v1/receipts/+12025550100"
        return web.Response(status=status)

    app = web.Application()
    app.router.add_post("/proxy/v1/receipts/{account}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/proxy/")))
        if status == 204:
            await client.send_read_receipt("+12025550100", "sender-uuid", 123)
        else:
            with pytest.raises(InvalidAuth if status == 401 else SignalError):
                await client.send_read_receipt("+12025550100", "sender-uuid", 123)
    assert seen == [
        {"recipient": "sender-uuid", "receipt_type": "read", "timestamp": 123}
    ]


@pytest.mark.parametrize("recipient", ["+12025550101", "group.WVdKag=="])
async def test_typing_contract(aiohttp_server, recipient):
    seen = []

    async def handler(request):
        seen.append((request.method, await request.json()))
        assert request.path == "/proxy/v1/typing-indicator/+12025550100"
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/proxy/v1/typing-indicator/{account}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/proxy/")))
        await client.set_typing("+12025550100", recipient, True)
        await client.set_typing("+12025550100", recipient, False)
    assert seen == [
        ("PUT", {"recipient": recipient}),
        ("DELETE", {"recipient": recipient}),
    ]
