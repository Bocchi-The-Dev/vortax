"""Download a source file, resumably, with a fallback chain.

Order: aria2c -> curl -> wget -> pure-Python (urllib). Each tool resumes a partial
file, so re-running after a dropped connection continues instead of restarting.
Local paths and file:// URLs are used in place (nothing is copied).
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import VortaxError

UA = "Mozilla/5.0 (X11; Linux x86_64) vortax"


def is_local(source: str) -> bool:
    return source.startswith("file://") or source.startswith("/") or source.startswith("./") \
        or source.startswith("~") or (len(source) > 1 and source[1] == ":" and source[0].isalpha())


def local_path(source: str) -> Path:
    if source.startswith("file://"):
        source = urllib.parse.unquote(urllib.parse.urlparse(source).path)
    return Path(source).expanduser()


def cache_name(url: str) -> str:
    """Stable file name for a URL: <sha1[:12]>-<basename>."""
    parsed = urllib.parse.urlparse(url)
    base = os.path.basename(urllib.parse.unquote(parsed.path).rstrip("/")) or "source"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    return f"{hashlib.sha1(url.encode()).hexdigest()[:12]}-{base}"


# --- MediaFire -----------------------------------------------------------------

_MF_DIRECT = re.compile(r'href="(https?://download[0-9]*\.mediafire\.com/[^"]+)"')
_MF_SCRAMBLED = re.compile(r'data-scrambled-url="([A-Za-z0-9+/=]+)"')


def parse_mediafire(page_html: str) -> str:
    """Extract the real download link from a MediaFire file page (pure, testable)."""
    m = _MF_SCRAMBLED.search(page_html)
    if m:
        try:
            url = base64.b64decode(m.group(1)).decode()
            if url.startswith("http"):
                return html.unescape(url)
        except (ValueError, UnicodeDecodeError):
            pass
    m = _MF_DIRECT.search(page_html)
    if m:
        return html.unescape(m.group(1))
    raise VortaxError("could not find a download link on the MediaFire page (file removed, or the page layout changed)")


def resolve_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("mediafire.com") and "/file/" in url:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = resp.read(2_000_000).decode("utf-8", "replace")
        except OSError as exc:
            raise VortaxError(f"MediaFire page fetch failed: {exc}") from None
        return parse_mediafire(page)
    return url


# --- download chain ---------------------------------------------------------------


def candidate_commands(url: str, dest: Path, have: dict[str, str | None] | None = None) -> list[tuple[str, list[str]]]:
    """The external download commands to try, in order (pure, testable)."""
    have = {n: shutil.which(n) for n in ("aria2c", "curl", "wget")} if have is None else have
    cmds: list[tuple[str, list[str]]] = []
    if have.get("aria2c"):
        cmds.append(("aria2c", ["aria2c", "-x", "16", "-s", "16", "--continue=true", "--allow-overwrite=true",
                                "--auto-file-renaming=false", f"--user-agent={UA}", "-d", str(dest.parent),
                                "-o", dest.name, url]))
    if have.get("curl"):
        cmds.append(("curl", ["curl", "-fL", "--retry", "5", "--retry-delay", "3", "-C", "-", "-A", UA,
                              "-o", str(dest), url]))
    if have.get("wget"):
        cmds.append(("wget", ["wget", "-c", "--tries=5", f"--user-agent={UA}", "-O", str(dest), url]))
    return cmds


def _urllib_download(url: str, dest: Path) -> None:
    have = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:           # already complete
            return
        raise
    mode = "ab" if (have and resp.status == 206) else "wb"
    with resp, open(dest, mode) as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)


def download(source: str, cache: Path, sha256: str = "", log=print) -> Path:
    """Return a local path to the source, downloading into `cache` when it is a URL."""
    if is_local(source):
        path = local_path(source)
        if not path.is_file():
            raise VortaxError(f"local source not found: {path}")
        return path
    if not source.startswith(("http://", "https://")):
        raise VortaxError(f"unsupported source {source!r}: use an http(s) URL or a local path")

    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / cache_name(source)
    done_marker = dest.with_name(dest.name + ".done")
    if done_marker.exists() and dest.exists():
        log(f"using cached download {dest.name}")
        return dest

    real = resolve_url(source)
    errors = []
    ok = False
    for tool, cmd in candidate_commands(real, dest):
        log(f"downloading with {tool}")
        try:
            rc = subprocess.run(cmd).returncode
        except OSError as exc:
            errors.append(f"{tool}: {exc}")
            continue
        if rc == 0 and dest.exists() and dest.stat().st_size > 0:
            ok = True
            break
        errors.append(f"{tool}: exit {rc}")
        log(f"{tool} failed (exit {rc}); trying the next method")
    if not ok:
        log("downloading with python urllib")
        try:
            _urllib_download(real, dest)
            ok = dest.exists() and dest.stat().st_size > 0
        except (OSError, ValueError) as exc:
            errors.append(f"urllib: {exc}")
    if not ok:
        raise VortaxError("download failed (" + "; ".join(errors) + "). Re-run to resume, or pass --source <local file>.")

    if sha256:
        digest = sha256_file(dest)
        if digest.lower() != sha256.lower():
            dest.unlink(missing_ok=True)
            raise VortaxError(f"checksum mismatch for {dest.name}: expected {sha256}, got {digest}")
    done_marker.write_text("ok\n")
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def head_status(url: str, timeout: float = 15.0) -> tuple[int, str]:
    """HEAD request for `vortax check-links`; falls back to a ranged GET when HEAD is refused."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.geturl()
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue
            return exc.code, url
        except OSError as exc:
            return 0, str(exc)
    return 0, "unreachable"


def log_stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
