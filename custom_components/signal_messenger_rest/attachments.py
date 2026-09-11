"""Bounded attachment reads honoring Home Assistant allowlists."""

import base64
import mimetypes
import os
import re
import stat
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .const import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, MAX_TOTAL_BYTES


def _local_file(hass: HomeAssistant, filename: str) -> tuple[bytes, str]:
    path = Path(filename).resolve()
    if not hass.config.is_allowed_path(str(path)):
        raise ServiceValidationError(
            "Attachment path is not in allowlist_external_dirs"
        )
    # Nonblocking open also prevents a configured FIFO from hanging the executor.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ServiceValidationError("Attachment must be a regular file")
        data = handle.read(MAX_ATTACHMENT_BYTES + 1)
    return data, path.name


async def _remote_file(hass: HomeAssistant, url: str) -> tuple[bytes, str]:
    session = async_get_clientsession(hass)
    for _ in range(6):
        parsed = URL(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.user is not None
            or not hass.config.is_allowed_external_url(url)
        ):
            raise ServiceValidationError(
                "Attachment URL is not in allowlist_external_urls"
            )
        async with session.get(
            url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise ServiceValidationError(
                        "Attachment redirect has no destination"
                    )
                url = str(parsed.join(URL(location)))
                continue
            if response.status != 200:
                raise ServiceValidationError("Attachment download failed")
            if (
                response.content_length
                and response.content_length > MAX_ATTACHMENT_BYTES
            ):
                raise ServiceValidationError("Attachment exceeds 10 MiB")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_ATTACHMENT_BYTES:
                    raise ServiceValidationError("Attachment exceeds 10 MiB")
            return bytes(data), Path(parsed.path).name or "attachment"
    raise ServiceValidationError("Too many attachment redirects")


async def encode_attachments(
    hass: HomeAssistant, paths: list[str], urls: list[str]
) -> list[str]:
    if len(paths) + len(urls) > MAX_ATTACHMENTS:
        raise ServiceValidationError("At most five attachments are allowed")
    result = []
    total = 0
    for remote, source in [(False, p) for p in paths] + [(True, u) for u in urls]:
        try:
            if remote:
                data, filename = await _remote_file(hass, source)
            else:
                data, filename = await hass.async_add_executor_job(
                    _local_file, hass, source
                )
        except OSError, ValueError, aiohttp.ClientError, TimeoutError:
            raise ServiceValidationError("Cannot read attachment") from None
        total += len(data)
        if len(data) > MAX_ATTACHMENT_BYTES or total > MAX_TOTAL_BYTES:
            raise ServiceValidationError(
                "Attachments exceed 10 MiB each or 20 MiB combined"
            )
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:128]
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        encoded = await hass.async_add_executor_job(base64.b64encode, data)
        result.append(
            f"data:{mime};filename={filename};base64,{encoded.decode('ascii')}"
        )
    return result
