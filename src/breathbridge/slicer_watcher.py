"""Watch PrusaLink for new print jobs and extract chamber temp from the gcode.

Polls /api/v1/job for filename changes. When a new file appears, fetches the
last ~50KB of it via HTTP Range and looks for the chamber target temperature
(M141 in start-gcode, or `; chamber_temperature = N` in the PrusaSlicer config
block at the end).

Requires bgcode disabled in the slicer — binary gcode is not parsed here.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)


# PrusaSlicer writes per-tool CSV values on multi-tool printers (e.g. XL):
#     ; chamber_temperature = 50,0,50,0,0
# So we capture the whole RHS and parse it ourselves.
_RE_CONFIG_CHAMBER = re.compile(
    r"^\s*;\s*chamber_temperature\s*=\s*([0-9.,\s-]+?)\s*$",
    re.MULTILINE,
)
_RE_M141 = re.compile(r"^\s*M141\s+S(-?\d+(?:\.\d+)?)", re.MULTILINE | re.IGNORECASE)


def _max_positive(csv: str) -> float | None:
    """Parse a comma-separated list of numbers and return the max positive.

    Used because PrusaSlicer emits per-tool CSV values like "50,0,50,0,0";
    inactive tools report 0. Returns None if all values are 0 or invalid.
    """
    best: float | None = None
    for part in csv.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = float(part)
        except ValueError:
            continue
        if val > 0 and (best is None or val > best):
            best = val
    return best


def parse_chamber_temp(text: str) -> int | None:
    """Extract the chamber target (°C) from gcode text.

    Precedence: `; chamber_temperature = N[,N,...]` from PrusaSlicer's config
    block > last `M141 S<n>` in the text > None.

    For CSV values (multi-tool printers), takes the max positive value across
    all tools — inactive tools report 0.

    Returns an int rounded down (Panda's set_temp is integer °C). A value of 0
    means "no chamber heating" and is treated as "no temp detected" so we
    don't accidentally write 0 into the Panda's setpoint.
    """
    config_matches = _RE_CONFIG_CHAMBER.findall(text)
    if config_matches:
        val = _max_positive(config_matches[-1])
        if val is not None:
            return int(val)

    m141_matches = _RE_M141.findall(text)
    if m141_matches:
        try:
            val = float(m141_matches[-1])
            if val > 0:
                return int(val)
        except ValueError:
            pass

    return None


_CONN_ERRS = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientConnectionError,
    asyncio.TimeoutError,
)


class SlicerWatcher:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        poll_interval: float,
        tail_bytes: int,
        on_detect: Callable[[str, int | None], Awaitable[None]],
        on_print_ended: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._tail_bytes = tail_bytes
        self._on_detect = on_detect
        self._on_print_ended = on_print_ended
        self._last_filename: str | None = None
        self._last_fetch_succeeded = False
        # None until first poll; True/False afterwards. Used to log connect
        # errors once per disconnect event instead of on every poll.
        self._reachable: bool | None = None

    async def run(self, shutdown: asyncio.Event) -> None:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self._poll_interval + 10)

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            while not shutdown.is_set():
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError:
                    raise
                except _CONN_ERRS as exc:
                    if self._reachable is not False:
                        logger.warning(
                            "SlicerWatcher: %s unreachable (%s: %s) — will retry silently",
                            self._base_url,
                            exc.__class__.__name__,
                            exc,
                        )
                        self._reachable = False
                    else:
                        logger.debug("SlicerWatcher still unreachable: %s", exc)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "SlicerWatcher poll failed: %s: %s",
                        exc.__class__.__name__,
                        exc,
                    )
                else:
                    if self._reachable is False:
                        logger.info("SlicerWatcher: %s reachable again", self._base_url)
                    self._reachable = True

                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        async with session.get(f"{self._base_url}/api/v1/job") as resp:
            if resp.status == 204:
                # No active job. Reset so we re-detect the next print.
                if self._last_filename is not None:
                    self._last_filename = None
                    self._last_fetch_succeeded = False
                    if self._on_print_ended is not None:
                        await self._on_print_ended()
                return
            if resp.status == 401:
                logger.error("PrusaLink rejected the API key (slicer watcher)")
                return
            if resp.status >= 400:
                logger.warning("PrusaLink /api/v1/job returned HTTP %d", resp.status)
                return
            job = await resp.json()

        file_info = job.get("file") or {}
        # Identity = path + name, so we re-detect when PrusaLink fills in a
        # partial response on a later poll (e.g. path is just the storage root
        # at print start, then becomes a full path).
        identity = f"{file_info.get('path','')}|{file_info.get('name','')}"
        if identity == "|":
            return
        if identity == self._last_filename:
            return

        urls = self._build_file_url_candidates(file_info)
        if not urls:
            logger.debug("Job seen but no fetchable URL yet: %s", file_info)
            return

        # Don't lock in the identity until we know we can actually fetch.
        logger.info("New print detected — full file info: %s", file_info)
        logger.debug("URL candidates: %s", urls)

        file_size = file_info.get("size")
        if file_size:
            logger.info(
                "Fetching gcode tail (%.1f MB total, ~2-3 min on PrusaLink USB)…",
                file_size / 1_000_000,
            )
        chamber, url = await self._fetch_chamber_temp(session, urls, file_size)
        if not self._last_fetch_succeeded:
            # Nothing worked — leave _last_filename alone so we retry next poll
            # in case PrusaLink hadn't fully published the file yet.
            return
        if chamber is None:
            logger.info("No chamber temp found in %s", url)
        else:
            logger.info("Parsed chamber temp from %s: %d°C", url, chamber)

        self._last_filename = identity
        display = (
            file_info.get("display_name")
            or file_info.get("name")
            or file_info.get("path")
            or "?"
        )
        await self._on_detect(display, chamber)

    async def _fetch_chamber_temp(
        self,
        session: aiohttp.ClientSession,
        urls: list[str],
        file_size: int | None = None,
    ) -> tuple[int | None, str]:
        """Try each URL until one succeeds. Returns (chamber, url_used).

        Range strategy:
        - If we know `file_size`, request an absolute range (bytes=<start>-<end>).
          PrusaLink honors absolute ranges even when it ignores suffix ranges.
        - Otherwise fall back to a suffix range (bytes=-<n>).
        - If the server returns 200 (Range ignored), we stream the whole file
          and keep only the last `tail_bytes` in a rolling buffer.
        """
        self._last_fetch_succeeded = False
        if file_size and file_size > self._tail_bytes:
            start = file_size - self._tail_bytes
            end = file_size - 1
            headers = {"Range": f"bytes={start}-{end}"}
        else:
            headers = {"Range": f"bytes=-{self._tail_bytes}"}
        # 180s per request — covers worst-case PrusaLink streaming a large file
        # off slow USB storage. The session-level timeout would cut this short,
        # so we override per request.
        per_req_timeout = aiohttp.ClientTimeout(total=180)
        last_status = 0
        last_exc: Exception | None = None

        for url in urls:
            try:
                async with session.get(url, headers=headers, timeout=per_req_timeout) as resp:
                    if resp.status not in (200, 206):
                        last_status = resp.status
                        logger.debug("Fetch HTTP %d for %s — trying next", resp.status, url)
                        continue
                    data = await self._read_tail_streaming(resp)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                logger.debug("Fetch error for %s: %s: %s", url, exc.__class__.__name__, exc)
                continue

            text = data.decode("utf-8", errors="ignore")
            logger.debug(
                "Fetched %d bytes from %s (HTTP %d). First 80 chars: %r",
                len(data), url, resp.status, text[:80],
            )
            self._last_fetch_succeeded = True
            return parse_chamber_temp(text), url

        if last_exc is not None:
            logger.warning(
                "All fetch URLs errored (last %s: %s). Tried: %s",
                last_exc.__class__.__name__,
                last_exc,
                urls,
            )
        else:
            logger.warning(
                "All fetch URLs returned HTTP errors (last status %d). Tried: %s",
                last_status,
                urls,
            )
        return None, urls[-1] if urls else ""

    async def _read_tail_streaming(self, resp: aiohttp.ClientResponse) -> bytes:
        """Read response body, keeping only the last tail_bytes in memory."""
        keep = self._tail_bytes
        buf = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            buf.extend(chunk)
            if len(buf) > keep * 4:
                # Trim periodically to bound memory.
                del buf[: len(buf) - keep]
        if len(buf) > keep:
            del buf[: len(buf) - keep]
        return bytes(buf)

    def _build_file_url_candidates(self, file_info: dict) -> list[str]:
        """Return URL candidates to try in order.

        Priority:
        1. The canonical `refs.download` URL the server gives us (most reliable).
        2. Direct `/<storage>/<file>` (newer PrusaLink web UI default).
        3. v1 API: `/api/v1/files/<storage>/<path>/raw`.
        4. Legacy OctoPrint-compat: `/api/files/<storage>/<path>/raw`.
        """
        urls: list[str] = []

        # 1. Canonical download from server if available
        refs = file_info.get("refs") or {}
        download = refs.get("download")
        if download:
            if download.startswith(("http://", "https://")):
                urls.append(download)
            else:
                urls.append(f"{self._base_url}/{download.lstrip('/')}")

        # 2-4. Constructed paths as fallback
        path = (file_info.get("path") or "").lstrip("/")
        name = (file_info.get("name") or "").lstrip("/")
        origin = (file_info.get("origin") or "").lower()

        storage_prefixes = ("usb", "local", "sdcard")
        first = path.split("/", 1)[0] if path else ""
        full: str | None = None
        if "/" in path and first in storage_prefixes:
            full = path
        elif path in storage_prefixes:
            if name:
                full = f"{path}/{name}"
        elif path:
            storage = origin if origin in storage_prefixes else "local"
            full = f"{storage}/{path}"
        elif name:
            storage = origin if origin in storage_prefixes else "local"
            full = f"{storage}/{name}"

        if full:
            quoted = quote(full, safe="/")
            for candidate in (
                f"{self._base_url}/{quoted}",
                f"{self._base_url}/api/v1/files/{quoted}/raw",
                f"{self._base_url}/api/files/{quoted}/raw",
            ):
                if candidate not in urls:
                    urls.append(candidate)

        return urls
