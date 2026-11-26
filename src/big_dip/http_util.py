"""带重试的 HTTP GET。默认不把响应写进仓库。"""

from __future__ import annotations

import hashlib
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def http_get(
    url: str,
    *,
    referer: str | None = None,
    timeout: float = 60,
    retries: int = 5,
    cache_dir: Path | None = None,
    cache_ttl: float = 3 * 3600,
    user_agent: str | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> bytes:
    """下载 URL。cache_dir 只用于同一次调试里的重复运行，调用方不应把缓存提交进 git。"""
    cache_path: Path | None = None
    stamp_path: Path | None = None
    if cache_dir is not None:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_path = cache_dir / digest
        stamp_path = cache_dir / f"{digest}.stamp"
        if cache_path.exists() and stamp_path.exists():
            age = time.time() - float(stamp_path.read_text(encoding="utf-8"))
            if age < cache_ttl:
                return cache_path.read_bytes()

    headers = {"User-Agent": user_agent or USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
                payload = response.read()
            if cache_path is not None and stamp_path is not None and cache_dir is not None:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(payload)
                stamp_path.write_text(str(time.time()), encoding="utf-8")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(1.2 * (2**attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
    assert last_error is not None
    raise last_error
