"""Tiny PNG images without Pillow, for the print preview and thumbnails."""
from __future__ import annotations

import hashlib
import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def preview_png(seed: str, width: int = 300, height: int = 300) -> bytes:
    """A recognisable image per job: a gradient in a colour derived from `seed`,
    with a darker square where the part would be."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    base = (digest[0], digest[1], digest[2])
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter: none
        for x in range(width):
            inside = width // 4 < x < 3 * width // 4 and height // 4 < y < 3 * height // 4
            shade = 0.45 if inside else 0.75 + 0.25 * (y / height)
            row += bytes(int(c * shade) for c in base)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 6)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", raw) + _chunk(b"IEND", b"")
