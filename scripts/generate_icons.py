"""Generate the console's original geometric bolt icons using only stdlib."""
from __future__ import annotations

import pathlib
import struct
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "console" / "web"
POINTS = [(295, 65), (140, 280), (230, 280), (200, 445), (375, 220), (275, 220)]


def inside(x, y):
    hit = False
    for (ax, ay), (bx, by) in zip(POINTS, POINTS[1:] + POINTS[:1]):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
    return hit


def chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def png(size):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend((183, 196, 255) if inside((x + .5) * 512 / size,
                                                (y + .5) * 512 / size)
                        else (22, 22, 26))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def main():
    (ROOT / "favicon.png").write_bytes(png(16))
    for size in (192, 512):
        for prefix in ("Icon", "Icon-maskable"):
            (ROOT / "icons" / f"{prefix}-{size}.png").write_bytes(png(size))
    points = " ".join(f"{x},{y}" for x, y in POINTS)
    (ROOT / "icons" / "adkl-bolt.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        'role="img" aria-label="ADKL lightning bolt">\n'
        '  <rect width="512" height="512" rx="96" fill="#16161A"/>\n'
        f'  <polygon points="{points}" fill="#B7C4FF"/>\n</svg>\n')


if __name__ == "__main__":
    main()
