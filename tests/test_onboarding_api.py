"""HTTP contracts for accounts, provisioning and linked-device administration."""

import aiohttp
import pytest
from aiohttp import web

from custom_components.signal_messenger_rest.api import (
    InvalidAuth,
    InvalidResponse,
    SignalClient,
    validate_link_uri,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")
URI = "sgnl://linkdevice?uuid=test-uuid&pub_key=test-key"


async def test_account_device_contract(aiohttp_server):
    seen = []

    async def handler(request):
        seen.append(
            (
                request.method,
                request.path,
                dict(request.query),
                await request.json() if request.can_read_body else None,
            )
        )
        assert (
            request.headers["Authorization"]
            == aiohttp.BasicAuth("proxy", "secret").encode()
        )
        if request.path.endswith("/accounts"):
            return web.json_response(["+123", "+456"])
        if request.path.endswith("/raw"):
            return web.json_response({"device_link_uri": URI})
        if request.method == "GET":
            return web.json_response(
                [
                    {
                        "id": 2,
                        "name": "Desktop",
                        "creation_timestamp": 1000,
                        "last_seen_timestamp": 2000,
                    }
                ]
            )
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/prefix/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(
            session, str(server.make_url("/prefix")), "proxy", "secret"
        )
        assert await client.accounts() == ["+123", "+456"]
        assert await client.start_link("HA & Signal") == URI
        assert (await client.devices("+123"))[0]["id"] == 2
        await client.add_device("+123", URI)
        await client.remove_device("+123", 2)
    assert seen == [
        ("GET", "/prefix/v1/accounts", {}, None),
        ("GET", "/prefix/v1/qrcodelink/raw", {"device_name": "HA & Signal"}, None),
        ("GET", "/prefix/v1/devices/+123", {}, None),
        ("POST", "/prefix/v1/devices/+123", {}, {"uri": URI}),
        ("DELETE", "/prefix/v1/devices/+123/2", {}, None),
    ]


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("accounts", {}),
        ("accounts", [None]),
        ("start_link", {"device_link_uri": "https://example.com/secret"}),
        ("start_link", None),
        ("devices", [{"id": True}]),
        ("devices", [{"id": 0}]),
        ("devices", [{"id": "2"}]),
    ],
)
async def test_invalid_contract(aiohttp_server, method, response):
    async def handler(request):
        return web.json_response(response)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        with pytest.raises(InvalidResponse):
            await getattr(client, method)(*([] if method == "accounts" else ["test"]))


async def test_link_auth_failure_not_retried(aiohttp_server):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return web.Response(status=401, text="secret provisioning credentials")

    app = web.Application()
    app.router.add_get("/v1/qrcodelink/raw", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SignalClient(session, str(server.make_url("/")))
        with pytest.raises(InvalidAuth) as error:
            await client.start_link("Home Assistant")
        assert "secret" not in str(error.value)
    assert calls == 1


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com",
        "sgnl://wrong?uuid=x&pub_key=y",
        "sgnl://linkdevice?uuid=x",
        None,
    ],
)
def test_invalid_uri(uri):
    with pytest.raises(ValueError, match="Invalid Signal device link URI"):
        validate_link_uri(uri)


async def test_primary_device_cannot_be_removed():
    with pytest.raises(ValueError):
        await SignalClient(None, "http://signal.test").remove_device("+123", 1)
