"""Static asset helpers: cached content revisions and compressed delivery.

Pages fingerprint every stylesheet and script with a hash of its bytes, and
each open tab polls the release manifest, which hashes the whole static and
template trees. Re-reading those files on every call made each page render
and each poll pay for disk reads and SHA-256 over megabytes of assets, so
revisions are cached per file and only recomputed when the file's stat
signature (mtime, size, inode) changes. Hot-deployed files therefore still
get a fresh revision.

``CompressedStaticFiles`` serves text assets gzip-compressed to browsers that
accept it. Static assets carry no per-user secrets, so compressing them does
not expose anything to compression side channels (BREACH); dynamic pages and
API responses are deliberately left alone.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from mimetypes import guess_type
from pathlib import Path
import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_Signature = tuple[int, int, int]

_content_hashes: dict[str, tuple[_Signature, str]] = {}
_compressed_bodies: dict[str, tuple[_Signature, bytes | None]] = {}

_COMPRESSIBLE_PREFIXES = ("text/",)
_COMPRESSIBLE_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/xml",
        "image/svg+xml",
    }
)
# Below this the gzip framing outweighs the saving; above it the file is not
# a typical asset and is better streamed as-is than held in memory.
_MIN_COMPRESS_BYTES = 1024
_MAX_COMPRESS_BYTES = 8 * 1024 * 1024
_GZIP_LEVEL = 6


def _signature(stat_result: os.stat_result) -> _Signature:
    return (stat_result.st_mtime_ns, stat_result.st_size, stat_result.st_ino)


def content_hash(path: Path) -> str:
    """Return a short SHA-256 revision of ``path``'s bytes, or "" if unreadable."""

    try:
        signature = _signature(path.stat())
    except OSError:
        return ""
    return content_hash_for(str(path), signature)


def content_hash_for(path: str, signature: _Signature) -> str:
    """Return the revision of ``path`` whose current stat signature is ``signature``."""

    cached = _content_hashes.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:16]
            read_signature = _signature(os.fstat(handle.fileno()))
    except OSError:
        return ""
    # Only cache when the file did not change while it was being read.
    if read_signature == signature:
        _content_hashes[path] = (signature, digest)
    return digest


def release_files(
    root: Path, *, skip_dirs: frozenset[str] = frozenset()
) -> list[tuple[str, str, _Signature]]:
    """Return ``(relative posix path, path, stat signature)`` for files under ``root``.

    Results are sorted by relative path. Symlinked directories are not
    descended into, and ``skip_dirs`` names top-level directories that are
    pruned without being walked, such as user uploads or cached installers
    that are not part of a release.
    """

    found: list[tuple[str, str, _Signature]] = []
    pending = [(str(root), "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as entries:
                listing = list(entries)
        except OSError:
            continue
        for entry in listing:
            relative = prefix + entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if prefix or entry.name not in skip_dirs:
                        pending.append((entry.path, relative + "/"))
                elif entry.is_file():
                    found.append((relative, entry.path, _signature(entry.stat())))
            except OSError:
                continue
    found.sort()
    return found


def is_compressible(media_type: str | None) -> bool:
    base = (media_type or "").split(";", 1)[0].strip().lower()
    return base.startswith(_COMPRESSIBLE_PREFIXES) or base in _COMPRESSIBLE_TYPES


def accepts_gzip(headers: Headers) -> bool:
    """Return whether the request's ``Accept-Encoding`` allows gzip."""

    for item in headers.get("accept-encoding", "").split(","):
        coding, _, params = item.partition(";")
        if coding.strip().lower() != "gzip":
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def _compressed_body(path: str, signature: _Signature) -> bytes | None:
    """Return the cached gzip body for ``path``, or None when it does not shrink."""

    cached = _compressed_bodies.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with open(path, "rb") as handle:
        data = handle.read()
        read_signature = _signature(os.fstat(handle.fileno()))
    if read_signature != signature:
        # Replaced since the request looked it up; the caller serves the file
        # uncompressed so headers and body cannot disagree.
        return None
    body: bytes | None = gzip.compress(data, compresslevel=_GZIP_LEVEL, mtime=0)
    if len(body) >= len(data):
        body = None
    _compressed_bodies[path] = (signature, body)
    return body


def _add_vary_accept_encoding(response: Response) -> None:
    vary = response.headers.get("vary", "")
    if "accept-encoding" not in vary.lower():
        response.headers["vary"] = f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"


class CompressedStaticFiles(StaticFiles):
    """StaticFiles that gzip-compresses text assets for clients that accept it.

    Each file is compressed once per process and cached until its stat
    signature changes. Range and HEAD requests, binary files and very small or
    very large files are served unchanged.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code not in (200, 304) or not is_compressible(guess_type(path)[0]):
            return response
        _add_vary_accept_encoding(response)
        if not isinstance(response, FileResponse):
            return response
        request_headers = Headers(scope=scope)
        stat_result = response.stat_result
        if (
            scope.get("method") != "GET"
            or "range" in request_headers
            or not accepts_gzip(request_headers)
            or stat_result is None
            or not _MIN_COMPRESS_BYTES <= stat_result.st_size <= _MAX_COMPRESS_BYTES
        ):
            return response
        try:
            body = await anyio.to_thread.run_sync(
                _compressed_body, os.fspath(response.path), _signature(stat_result)
            )
        except OSError:
            return response
        if body is None:
            return response
        headers = {
            name: value
            for name, value in response.headers.items()
            if name not in ("content-length", "accept-ranges")
        }
        headers["content-encoding"] = "gzip"
        etag = headers.get("etag")
        if etag and not etag.startswith("W/"):
            # The compressed bytes differ from the file, so the strong
            # validator would be wrong; a weak one still revalidates.
            headers["etag"] = f"W/{etag}"
        return Response(body, status_code=200, headers=headers)
