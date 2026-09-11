"""Attachment allowlists, redirects, memory limits and filenames."""

from unittest.mock import patch

import pytest
from aiohttp import web
from homeassistant.exceptions import ServiceValidationError

from custom_components.signal_messenger_rest.attachments import encode_attachments


async def test_local(hass, tmp_path):
    file = tmp_path / "snapshot.jpg"
    file.write_bytes(b"example")
    with pytest.raises(ServiceValidationError, match="allowlist"):
        await encode_attachments(hass, [str(file)], [])
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    encoded = await encode_attachments(hass, [str(file)], [])
    assert encoded == ["data:image/jpeg;filename=snapshot.jpg;base64,ZXhhbXBsZQ=="]
    with patch(
        "custom_components.signal_messenger_rest.attachments.MAX_ATTACHMENT_BYTES", 2
    ):
        with pytest.raises(ServiceValidationError):
            await encode_attachments(hass, [str(file)], [])
    with pytest.raises(ServiceValidationError):
        await encode_attachments(hass, [str(file)] * 6, [])


async def test_symlink_escape(hass, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("secret")
    (allowed / "link").symlink_to(secret)
    hass.config.allowlist_external_dirs = {str(allowed)}
    with pytest.raises(ServiceValidationError):
        await encode_attachments(hass, [str(allowed / "link")], [])


@pytest.mark.usefixtures("socket_enabled")
async def test_redirect_checked_before_download(hass, aiohttp_server):
    fetched = False

    async def redirect(request):
        raise web.HTTPFound("/private")

    async def private(request):
        nonlocal fetched
        fetched = True
        return web.Response(body=b"private")

    app = web.Application()
    app.router.add_get("/allowed", redirect)
    app.router.add_get("/private", private)
    server = await aiohttp_server(app)
    url = str(server.make_url("/allowed"))
    hass.config.allowlist_external_urls = {url}
    with pytest.raises(ServiceValidationError, match="allowlist"):
        await encode_attachments(hass, [], [url])
    assert not fetched


@pytest.mark.usefixtures("socket_enabled")
async def test_chunked_limit(hass, aiohttp_server):
    async def handler(request):
        response = web.StreamResponse()
        response.enable_chunked_encoding()
        await response.prepare(request)
        await response.write(b"more than two bytes")
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/file", handler)
    server = await aiohttp_server(app)
    url = str(server.make_url("/file"))
    hass.config.allowlist_external_urls = {url}
    with patch(
        "custom_components.signal_messenger_rest.attachments.MAX_ATTACHMENT_BYTES", 2
    ):
        with pytest.raises(ServiceValidationError, match="exceeds"):
            await encode_attachments(hass, [], [url])
