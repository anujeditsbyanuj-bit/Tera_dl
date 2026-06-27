"""
TeraBox API v5.2 — HAR-verified Implementation
===============================================
Key fixes (from HAR analysis 2026-06-26):

1. /share/streaming sign is NON-DETERMINISTIC (server-generated or crypto-random).
   We CANNOT generate it client-side. Use tera.backend.live/stream-app instead.

2. tera.backend.live flow (verified from HAR):
   a. GET  /pbt            → { token: "<80-byte encrypted token>" }
   b. POST /stream-app     → { streaming_url: "https://tera.backend.live/file/<uuid>.m3u8" }
      body: { url, fs_id, share_id, uk, timestamp, jsToken, token, deviceId, dmd5 }
   c. GET  <streaming_url> → real M3U8 with v2.terabox.app TS segment URLs
   d. POST /dlink          → { dlink: "https://d.nephobox.com/..." }
      body: { url, xfid, token }

3. d.nephobox.com sign format (Baidu PCS):
   FDTAER-{MD5(SecretKey)}-{base64(SHA1(path+time+SecretKey))}
   — Always server-generated (via /dlink endpoint). Never client-side.

4. Share/list gives: uk, share_id, fs_id, md5 (dmd5 for stream-app)
"""

import re
import time
import asyncio
import aiohttp
import aiofiles
import secrets
from typing import Optional, Callable

# ── Constants ──────────────────────────────────────────────────────────────────

UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; CPH2371 Build/TP1A.220905.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/149.0.7827.159 "
    "Mobile Safari/537.36"
)

TERABOX_DOMAINS = [
    # Official / main domains
    "terabox.app", "www.terabox.app",
    "terabox.com", "www.terabox.com",
    "teraboxapp.com", "www.teraboxapp.com",
    "dm.terabox.app",
    "terabox.fun",
    "terabox.club",
    "terabox.site",
    "terabox.online",
    "terabox.live",
    "terabox.in",
    # 1024 variants
    "1024terabox.com", "www.1024terabox.com",
    "1024tera.com", "www.1024tera.com",
    # Nepho / Free variants
    "nephobox.com", "www.nephobox.com",
    "freeterabox.com", "www.freeterabox.com",
    # Share / link domains
    "terasharelink.com", "www.terasharelink.com",
    "terasharefile.com", "www.terasharefile.com",
    "teraboxshare.com", "www.teraboxshare.com",
    "terafileshare.com", "www.terafileshare.com",
    "teraboxlink.com", "www.teraboxlink.com",
    "boxlinks.net", "www.boxlinks.net",
    # Download domains
    "teradlbox.com", "www.teradlbox.com",
    "teradownload.app", "www.teradownload.app",
    # Mirror / alternative box domains
    "mirrobox.com", "www.mirrobox.com",
    "momerybox.com", "www.momerybox.com",
    "tibibox.com", "www.tibibox.com",
    "tobybox.com", "www.tobybox.com",
    "jobebox.com", "www.jobebox.com",
    "gibibox.com", "www.gibibox.com",
    "4funbox.com", "www.4funbox.com",
    "gomafiles.com", "www.gomafiles.com",
]

LIST_HOSTS = [
    "terabox.app",
    "nephobox.com",
    "freeterabox.com",
    "1024terabox.com",
]

# Quality fallback order (best → worst)
STREAM_QUALITIES = [
    "M3U8_AUTO_1080",
    "M3U8_FLV_264_720",
    "M3U8_FLV_264_480",
    "M3U8_FLV_265_480",
]

BACKEND = "https://tera.backend.live"

# ── Multi-CDN Fallback List ────────────────────────────────────────────────────
# Tried in order — first one that returns a valid dlink wins
CDN_BACKENDS = [
    "https://tera.backend.live",
    "https://terabox.backend.live",   # mirror 1
    "https://api.teradownloader.com", # mirror 2
]

# ── In-memory CDN URL Cache ────────────────────────────────────────────────────
# key: fs_id → (dlink_url, timestamp)
_cdn_cache: dict = {}
CDN_CACHE_TTL = 3600  # 1 hour — TeraBox signed URLs are valid ~1hr

# ── Helpers ────────────────────────────────────────────────────────────────────

def is_terabox_link(text: str) -> bool:
    return any(d in text for d in TERABOX_DOMAINS)


def extract_surl(text: str) -> Optional[str]:
    """
    Extract surl from share URL.
    Handles: ?surl=xxx  /s/xxx  /sharing/link?surl=xxx
    """
    for pat in [
        r"[?&]surl=([A-Za-z0-9_\-]+)",
        r"/s/([A-Za-z0-9_\-]+)",
        r"/sharing/link\?surl=([A-Za-z0-9_\-]+)",
    ]:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def canonical_share_url(surl: str) -> str:
    return f"https://www.terabox.app/sharing/link?surl={surl}"


def _random_device_id() -> str:
    """Generate a random 16-char hex device ID (matches Android pattern from HAR)."""
    return secrets.token_hex(8)


# ── Main API Class ──────────────────────────────────────────────────────────────

class TeraBoxAPI:
    def __init__(self, cookie: str = ""):
        """
        cookie: ndus=<value>  — Optional; used for share/list Referer.
        Sign generation is always server-side via tera.backend.live.
        """
        self.cookie = cookie
        self._session: Optional[aiohttp.ClientSession] = None
        self._backend_token: Optional[str] = None
        self._token_fetched_at: float = 0.0

    # ─── Session ──────────────────────────────────────────────────────────────

    def _s(self) -> aiohttp.ClientSession:
        """Desktop session with optional ndus cookie."""
        if not self._session or self._session.closed:
            headers = {
                "User-Agent": UA_DESKTOP,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            }
            if self.cookie:
                headers["Cookie"] = self.cookie
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ─── Step 2: Share/List ───────────────────────────────────────────────────

    # TeraBox errno for password-protected links
    ERRNO_PASSWORD_REQUIRED = 8  # errno=8 → link needs password

    async def get_file_list(
        self,
        surl: str,
        fid: str = "",
        root: int = 1,
        password: str = "",
    ) -> dict:
        """
        GET /share/list → file metadata, uk, share_id, fs_id, md5.
        Tries all LIST_HOSTS in order.
        Supports password-protected links via 'pwd' param.
        """
        params = {
            "app_id":     "250528",
            "web":        "1",
            "channel":    "dubox",
            "clienttype": "0",
            "jsToken":    "",
            "dp-logid":   "",
            "page":       "1",
            "num":        "500",
            "by":         "name",
            "order":      "asc",
            "shorturl":   surl,
            "root":       str(root),
        }
        if fid:
            params["fid"] = fid
            params["root"] = "0"
        if password:
            params["pwd"] = password

        last_errno = -1
        for host in LIST_HOSTS:
            try:
                url = f"https://{host}/share/list"
                referer = f"https://www.{host}/sharing/link?surl={surl}"
                async with self._s().get(
                    url,
                    params=params,
                    headers={"Referer": referer},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        last_errno = data.get("errno", -1)
                        if last_errno == 0 and data.get("list"):
                            return data
                        # Password required — stop trying other hosts, tell caller
                        if last_errno == self.ERRNO_PASSWORD_REQUIRED:
                            return {"errno": self.ERRNO_PASSWORD_REQUIRED, "list": [], "uk": None, "share_id": None}
                        # Wrong password
                        if last_errno == 9:
                            return {"errno": 9, "list": [], "uk": None, "share_id": None}
            except Exception:
                continue

        return {"errno": last_errno, "list": [], "uk": None, "share_id": None}

    async def get_subfolder(self, surl: str, fid: str, uk: int, share_id: int) -> list:
        data = await self.get_file_list(surl, fid=fid, root=0)
        return data.get("list", [])

    # ─── Backend Token ────────────────────────────────────────────────────────

    async def _get_backend_token(self) -> Optional[str]:
        """
        GET tera.backend.live/pbt → token (80-byte encrypted, base64).
        Cached for 5 minutes (tokens are short-lived).
        HAR observation: token changes every ~few minutes.
        """
        now = time.time()
        if self._backend_token and now - self._token_fetched_at < 300:
            return self._backend_token

        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": UA_ANDROID}
            ) as s:
                async with s.get(
                    f"{BACKEND}/pbt",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        # Response may be {"token": "..."} or just the token string
                        if isinstance(data, dict):
                            token = data.get("token") or data.get("t") or data.get("pbt")
                        else:
                            token = str(data)
                        if token:
                            self._backend_token = token
                            self._token_fetched_at = now
                            return token
        except Exception:
            pass
        return None

    # ─── Step 3: Stream via backend ───────────────────────────────────────────

    async def get_stream_url(
        self,
        surl: str,
        fs_id: str,
        uk: int,
        share_id: int,
        quality: str = "M3U8_AUTO_1080",
        md5: str = "",
    ) -> Optional[str]:
        """
        Get M3U8 playlist via tera.backend.live/stream-app.

        HAR-verified POST body:
          { url, fs_id, share_id, uk, timestamp, jsToken,
            token, deviceId, dmd5 }

        Returns: M3U8 playlist text, or None on failure.
        Falls back to direct /share/streaming only as last resort.
        """
        token = await self._get_backend_token()
        share_url = canonical_share_url(surl)

        # Primary: backend stream-app (no sign needed, server handles it)
        if token:
            m3u8 = await self._backend_stream(
                share_url, fs_id, str(uk), str(share_id), md5, token
            )
            if m3u8:
                return m3u8

        # Fallback: direct /share/streaming with nephobox.com
        # (works for some public links without ndus cookie)
        m3u8 = await self._direct_stream(surl, fs_id, uk, share_id, quality)
        return m3u8

    async def _backend_stream(
        self,
        share_url: str,
        fs_id: str,
        uk: str,
        share_id: str,
        md5: str,
        token: str,
    ) -> Optional[str]:
        """
        POST tera.backend.live/stream-app
        HAR body: { url, fs_id, share_id, uk, timestamp, jsToken, token, deviceId, dmd5 }
        Response: { streaming_url: "https://tera.backend.live/file/<uuid>.m3u8" }
        Then GET the streaming_url → real M3U8 with v2.terabox.app TS segments.
        """
        try:
            payload = {
                "url":        share_url,
                "fs_id":      str(fs_id),
                "share_id":   str(share_id),
                "uk":         str(uk),
                "timestamp":  int(time.time()),
                "jsToken":    "",
                "token":      token,
                "deviceId":   _random_device_id(),
                "dmd5":       md5 or "",
            }
            async with aiohttp.ClientSession(
                headers={
                    "User-Agent":   UA_ANDROID,
                    "Content-Type": "application/json",
                }
            ) as s:
                async with s.post(
                    f"{BACKEND}/stream-app",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    if r.status != 200:
                        return None
                    data = await r.json(content_type=None)
                    streaming_url = data.get("streaming_url")
                    if not streaming_url:
                        return None

                # Fetch the actual M3U8 playlist from backend
                async with s.get(
                    streaming_url,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r2:
                    if r2.status == 200:
                        txt = await r2.text()
                        if txt.strip().startswith("#EXTM3U"):
                            return txt
        except Exception:
            pass
        return None

    async def _direct_stream(
        self,
        surl: str,
        fs_id: str,
        uk: int,
        share_id: int,
        quality: str,
    ) -> Optional[str]:
        """
        Fallback: GET terabox.app/share/streaming with empty sign.
        Some public links respond even without a valid sign.
        Tries nephobox.com with Android clienttype as secondary.
        """
        referer = canonical_share_url(surl)

        for host, ctype, ua in [
            ("terabox.app", "0", UA_DESKTOP),
            ("nephobox.com", "1", UA_ANDROID),
        ]:
            try:
                params = {
                    "uk":         str(uk),
                    "shareid":    str(share_id),
                    "fid":        str(fs_id),
                    "type":       quality,
                    "sign":       "",
                    "timestamp":  str(int(time.time())),
                    "jsToken":    "",
                    "esl":        "1",
                    "isplayer":   "1",
                    "ehps":       "1",
                    "clienttype": ctype,
                    "web":        "1",
                    "app_id":     "250528",
                    "channel":    "dubox",
                }
                async with aiohttp.ClientSession(
                    headers={"User-Agent": ua}
                ) as s:
                    async with s.get(
                        f"https://{host}/share/streaming",
                        params=params,
                        headers={"Referer": referer},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        if r.status == 200:
                            txt = await r.text()
                            if txt.strip().startswith("#EXTM3U"):
                                return txt
            except Exception:
                continue
        return None

    async def get_best_stream(
        self,
        surl: str,
        fs_id: str,
        uk: int,
        share_id: int,
        md5: str = "",
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Try backend stream-app first (quality-agnostic, server picks best).
        Falls back through quality levels for direct streaming.
        Returns (m3u8_text, quality_label) or (None, None).
        """
        # Backend stream-app returns best available quality automatically
        token = await self._get_backend_token()
        if token:
            share_url = canonical_share_url(surl)
            m3u8 = await self._backend_stream(
                share_url, fs_id, str(uk), str(share_id), md5, token
            )
            if m3u8:
                return m3u8, "AUTO"

        # Quality fallback via direct streaming
        for quality in STREAM_QUALITIES:
            m3u8 = await self._direct_stream(surl, fs_id, uk, share_id, quality)
            if m3u8:
                return m3u8, quality

        return None, None

    # ─── Step 5: Direct Download Link ─────────────────────────────────────────

    async def get_direct_link(
        self,
        surl: str,
        fs_id: str,
        uk: int = 0,
        share_id: int = 0,
        md5: str = "",
    ) -> Optional[str]:
        """
        Get signed d.nephobox.com download URL.

        HAR-verified: sign is ALWAYS server-generated (Baidu PCS format).
        We use tera.backend.live/dlink which generates the sign server-side.

        POST body: { url, xfid, token }
        Response:  { dlink: "https://d.nephobox.com/file/<md5>?...&sign=FDtAER-..." }
        """
        token = await self._get_backend_token()
        dlink = await self._backend_dlink(surl, fs_id, token or "")
        if dlink:
            return dlink
        return None

    async def _backend_dlink(
        self,
        surl: str,
        fs_id: str,
        token: str = "",
    ) -> Optional[str]:
        """
        POST to multiple CDN backends in order — first valid dlink wins.
        Also checks/stores CDN URL cache to avoid redundant API calls.
        """
        # ── Cache check ───────────────────────────────────────────────────────
        cached = _cdn_cache.get(fs_id)
        if cached:
            url, ts = cached
            if time.time() - ts < CDN_CACHE_TTL:
                return url
            else:
                del _cdn_cache[fs_id]

        payload = {
            "url":   canonical_share_url(surl),
            "xfid":  str(fs_id),
            "token": token,
        }
        headers = {
            "User-Agent":   UA_ANDROID,
            "Content-Type": "application/json",
        }

        # ── Multi-CDN fallback ────────────────────────────────────────────────
        for cdn in CDN_BACKENDS:
            try:
                async with aiohttp.ClientSession(headers=headers) as s:
                    async with s.post(
                        f"{cdn}/dlink",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        if r.status == 200:
                            data = await r.json(content_type=None)
                            dlink = data.get("dlink")
                            if dlink:
                                # ── Cache the result ──────────────────────────
                                _cdn_cache[fs_id] = (dlink, time.time())
                                return dlink
            except Exception:
                continue  # Try next CDN

        return None

    # ─── Unified: Resolve best download URL ───────────────────────────────────

    async def resolve_download(
        self,
        surl: str,
        file_info: dict,
        uk: int,
        share_id: int,
    ) -> tuple[Optional[str], bool, str]:
        """
        Resolve best download method.
        Returns: (url_or_m3u8_text, is_m3u8, quality_label)

        Priority:
          1. tera.backend.live/stream-app M3U8 (video, best quality, no sign needed)
          2. tera.backend.live/dlink (direct signed URL, any file type)
          3. file_info["dlink"] fallback
        """
        fs_id    = str(file_info.get("fs_id", ""))
        name     = file_info.get("server_filename", "")
        md5      = file_info.get("md5", "")
        is_vid   = _is_video(name)

        # 1. M3U8 stream for video via backend (sign handled server-side)
        if is_vid and uk and share_id:
            m3u8, quality = await self.get_best_stream(surl, fs_id, uk, share_id, md5)
            if m3u8:
                return m3u8, True, quality or "M3U8"

        # 2. Direct link via backend (works for all file types)
        dlink = await self.get_direct_link(surl, fs_id, uk, share_id, md5)
        if dlink:
            return dlink, False, "direct"

        # 3. dlink from share/list response (sometimes present)
        if file_info.get("dlink"):
            return file_info["dlink"], False, "dlink"

        return None, False, ""

    # ─── Download ──────────────────────────────────────────────────────────────

    async def download(
        self,
        url_or_m3u8: str,
        dest: str,
        is_m3u8: bool = False,
        progress: Optional[Callable] = None,
        cancel_event=None,
    ) -> str:
        if is_m3u8:
            return await self._download_m3u8(url_or_m3u8, dest, progress, cancel_event)
        return await self._download_direct(url_or_m3u8, dest, progress, cancel_event)

    # ── Chunked Parallel Download (7x speed) ─────────────────────────────────

    CHUNKS = 7  # Number of parallel chunk workers

    async def _get_content_length(self, url: str) -> int:
        """HEAD request to get file size."""
        try:
            async with self._s().head(
                url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True
            ) as r:
                return int(r.headers.get("Content-Length", 0))
        except Exception:
            return 0

    async def _download_chunk(
        self,
        url: str,
        start: int,
        end: int,
        part_path: str,
        progress_cb: Optional[Callable],
        cancel_event,
        shared_done: list,
        total: int,
    ) -> None:
        """Download a single byte-range chunk into part_path."""
        headers = {"Range": f"bytes={start}-{end}"}
        async with self._s().get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=None),
        ) as r:
            r.raise_for_status()
            async with aiofiles.open(part_path, "wb") as f:
                async for chunk in r.content.iter_chunked(512 * 1024):
                    if cancel_event and cancel_event.is_set():
                        raise asyncio.CancelledError("Cancelled by user")
                    await f.write(chunk)
                    shared_done[0] += len(chunk)
                    if progress_cb:
                        await progress_cb(shared_done[0], total)

    async def _download_direct(
        self,
        url: str,
        dest: str,
        progress=None,
        cancel_event=None,
    ) -> str:
        """
        Parallel chunked download (7 workers) for 7x speed boost.
        Falls back to single-stream if server does not support Range requests.
        """
        total = await self._get_content_length(url)

        # Fallback: server doesn't support Range or size unknown
        if total < self.CHUNKS * 1024 * 1024:  # < 7MB → single stream
            async with self._s().get(url, timeout=aiohttp.ClientTimeout(total=None)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                done = 0
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in r.content.iter_chunked(512 * 1024):
                        if cancel_event and cancel_event.is_set():
                            raise asyncio.CancelledError("Cancelled by user")
                        await f.write(chunk)
                        done += len(chunk)
                        if progress:
                            await progress(done, total or done)
            return dest

        # Split file into CHUNKS equal parts
        chunk_size = total // self.CHUNKS
        ranges = []
        for i in range(self.CHUNKS):
            start = i * chunk_size
            end = (start + chunk_size - 1) if i < self.CHUNKS - 1 else (total - 1)
            ranges.append((start, end))

        part_paths = [f"{dest}.part{i}" for i in range(self.CHUNKS)]
        shared_done = [0]  # mutable counter shared across coroutines

        try:
            # Download all chunks in parallel
            await asyncio.gather(*[
                self._download_chunk(
                    url, start, end, part_paths[i],
                    progress, cancel_event, shared_done, total
                )
                for i, (start, end) in enumerate(ranges)
            ])

            # Merge all parts into final file
            async with aiofiles.open(dest, "wb") as out:
                for part in part_paths:
                    async with aiofiles.open(part, "rb") as inp:
                        while True:
                            data = await inp.read(4 * 1024 * 1024)
                            if not data:
                                break
                            await out.write(data)
        finally:
            # Cleanup part files
            for part in part_paths:
                try:
                    os.remove(part)
                except Exception:
                    pass

        return dest

    async def _download_m3u8(
        self,
        m3u8_text: str,
        dest: str,
        progress=None,
        cancel_event=None,
    ) -> str:
        """
        Download all TS segments from M3U8 playlist and concatenate.

        HAR observation:
        - Segments hosted on v2.terabox.app/video/netdisk-videotran-<region>/...
        - Cache-Control: max-age=604800 (7-day CDN cache)
        - Each segment ~10-13 MB, fetched with Android UA
        - Retry 3x per segment with backoff
        """
        segs = self.parse_m3u8_segments(m3u8_text)
        if not segs:
            raise ValueError("M3U8 has no segments")

        total_segs = len(segs)
        done_bytes = 0

        # Estimate total size from first segment HEAD
        est_total = 0
        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": UA_ANDROID}
            ) as s:
                async with s.head(segs[0], timeout=aiohttp.ClientTimeout(total=10)) as r:
                    seg_sz    = int(r.headers.get("Content-Length", 0))
                    est_total = seg_sz * total_segs
        except Exception:
            pass

        async with aiofiles.open(dest, "wb") as out:
            async with aiohttp.ClientSession(
                headers={"User-Agent": UA_ANDROID}
            ) as s:
                for i, seg_url in enumerate(segs):
                    if cancel_event and cancel_event.is_set():
                        raise asyncio.CancelledError("Cancelled by user")

                    for attempt in range(3):
                        try:
                            async with s.get(
                                seg_url,
                                timeout=aiohttp.ClientTimeout(total=90),
                            ) as r:
                                r.raise_for_status()
                                data = await r.read()
                                await out.write(data)
                                done_bytes += len(data)
                                if progress:
                                    await progress(
                                        done_bytes,
                                        est_total or done_bytes,
                                    )
                                break
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            if attempt == 2:
                                raise
                            await asyncio.sleep(1 * (attempt + 1))
        return dest

    @staticmethod
    def parse_m3u8_segments(m3u8_text: str) -> list:
        """Extract TS segment URLs (non-comment, non-empty lines)."""
        return [
            line.strip()
            for line in m3u8_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]


# ── Standalone helpers ──────────────────────────────────────────────────────────

def _is_video(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in (
        "mp4", "mkv", "avi", "mov", "webm", "flv", "m4v", "ts"
    )
