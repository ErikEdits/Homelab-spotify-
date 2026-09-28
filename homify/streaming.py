"""Dateien mit HTTP-Range-Unterstützung ausliefern – auch direkt vom NAS (SMB)."""

from __future__ import annotations

import re
from email.utils import formatdate
from typing import BinaryIO, Callable

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

CHUNK = 256 * 1024
_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def ranged_response(request: Request, opener: Callable[[], BinaryIO], size: int, mtime: float,
                    media_type: str, headers: dict[str, str] | None = None) -> Response:
    out_headers = {
        "accept-ranges": "bytes",
        "etag": f'"{size:x}-{int(mtime):x}"',
        "last-modified": formatdate(mtime, usegmt=True),
        **(headers or {}),
    }
    start, end, status = 0, max(size - 1, 0), 200
    header = (request.headers.get("range") or "").strip()
    m = _RANGE.match(header) if header else None
    if m:
        first, last = m.groups()
        if first == "" and last:
            start = max(size - int(last), 0)
        else:
            start = int(first or 0)
            end = min(int(last), size - 1) if last else size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={"content-range": f"bytes */{size}"})
        status = 206
        out_headers["content-range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1 if size else 0
    out_headers["content-length"] = str(length)

    def body():
        if not length:
            return
        with opener() as fh:
            if start:
                fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=out_headers)
