"""Cover-Bilder: aus Dateien extrahieren, deduplizieren, Vorschaubilder und Hauptfarbe."""

from __future__ import annotations

import colorsys
import hashlib
import io
import os
import threading
from pathlib import Path

from . import db
from .config import DATA_DIR

try:  # Pillow ist optional – ohne gibt es einfach keine verkleinerten Bilder
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

COVER_DIR = DATA_DIR / "covers"
THUMB_DIR = COVER_DIR / "thumbs"
THUMB_SIZES = (64, 160, 300, 640)
FOLDER_IMAGES = (
    "cover", "folder", "front", "album", "albumart", "albumartsmall", "artwork", "art",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
DEFAULT_COLOR = "#535353"

_thumb_lock = threading.Lock()


def _ext_for(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    return None


def cover_path(cover_id: str, ext: str) -> Path:
    return COVER_DIR / cover_id[:2] / f"{cover_id}.{ext}"


def dominant_color(data: bytes) -> str:
    """Kräftige Hauptfarbe (für den Farbverlauf hinter Album-Headern, wie bei Spotify)."""
    if Image is None:
        return DEFAULT_COLOR
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((64, 64))
        quant = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette() or []
        counts = sorted(quant.getcolors() or [], reverse=True)
        best, best_score = None, -1.0
        for count, idx in counts:
            r, g, b = palette[idx * 3: idx * 3 + 3]
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            # Häufig + gesättigt + nicht zu hell/dunkel
            score = count * (0.35 + s) * (1 - abs(l - 0.45))
            if score > best_score:
                best, best_score = (h, l, s), score
        if best is None:
            return DEFAULT_COLOR
        h, l, s = best
        l = min(max(l, 0.25), 0.45)
        s = min(s, 0.75)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
    except Exception:
        return DEFAULT_COLOR


class CoverStore:
    """Speichert Cover genau einmal (Hash des Bildinhalts als ID)."""

    def __init__(self):
        self.known: dict[str, str] = {}
        self._folder_cache: dict[str, str | None] = {}
        self.load()

    def load(self) -> None:
        self.known = {row["id"]: row["ext"] for row in db.query("SELECT id, ext FROM covers")}

    def store(self, data: bytes | None) -> str | None:
        if not data or len(data) < 64:
            return None
        ext = _ext_for(data)
        if ext is None:
            return None
        cover_id = hashlib.sha1(data).hexdigest()[:20]
        if cover_id in self.known and cover_path(cover_id, self.known[cover_id]).exists():
            return cover_id
        path = cover_path(cover_id, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        color = dominant_color(data)
        db.execute(
            "INSERT OR REPLACE INTO covers (id, ext, color) VALUES (?, ?, ?)",
            (cover_id, ext, color),
        )
        self.known[cover_id] = ext
        return cover_id

    def folder_cover(self, st, rel_dir: str) -> str | None:
        """cover.jpg / folder.jpg usw. im Ordner des Songs (lokal oder auf dem NAS)."""
        cache_key = f"{st.key}|{rel_dir}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]
        result = None
        images = [(rel, size) for rel, size in st.dir_files(rel_dir)
                  if os.path.splitext(rel)[1].lower() in IMAGE_EXTS]

        def rank(item):
            stem = os.path.splitext(os.path.basename(item[0]))[0].lower()
            return (FOLDER_IMAGES.index(stem) if stem in FOLDER_IMAGES else 99, stem)

        for rel, size in sorted(images, key=rank)[:3]:
            if size > 15 * 1024 * 1024:
                continue
            try:
                with st.open(rel) as fh:
                    result = self.store(fh.read())
                if result:
                    break
            except OSError:
                continue
        self._folder_cache[cache_key] = result
        return result


def get_cover_file(cover_id: str, size: int | None) -> tuple[Path, str] | None:
    row = db.query_one("SELECT ext FROM covers WHERE id = ?", (cover_id,))
    if not row:
        return None
    original = cover_path(cover_id, row["ext"])
    if not original.exists():
        return None
    media = "image/jpeg" if row["ext"] == "jpg" else f"image/{row['ext']}"
    if not size or Image is None:
        return original, media
    size = min(THUMB_SIZES, key=lambda s: abs(s - size))
    thumb = THUMB_DIR / cover_id[:2] / f"{cover_id}_{size}.jpg"
    if thumb.exists():
        return thumb, "image/jpeg"
    with _thumb_lock:
        if not thumb.exists():
            try:
                img = Image.open(original)
                img = img.convert("RGB")
                if max(img.size) > size:
                    img.thumbnail((size, size), Image.Resampling.LANCZOS)
                thumb.parent.mkdir(parents=True, exist_ok=True)
                tmp = thumb.with_suffix(".tmp")
                img.save(tmp, "JPEG", quality=86, optimize=True)
                os.replace(tmp, thumb)
            except Exception:
                return original, media
    return thumb, "image/jpeg"


def colors_for(cover_ids: list[str]) -> dict[str, str]:
    ids = [c for c in set(cover_ids) if c]
    if not ids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for row in db.query(f"SELECT id, color FROM covers WHERE id IN ({marks})", chunk):
            out[row["id"]] = row["color"]
    return out
