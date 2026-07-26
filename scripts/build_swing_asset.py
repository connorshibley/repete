#!/usr/bin/env python3
"""Derive the shipped swing asset from the master illustration.

    python3 scripts/build_swing_asset.py

Reads  src/assets/repete_swing_master.png   (896x1200, opaque, white matte)
Writes src/assets/repete_swing.webp         (trimmed, alpha, ~30 KB)

Why this file exists
--------------------
The master is an AI-generated illustration with a *baked-in white background
and no alpha channel*. The dashboard hero it sits in is NOT white -- it is a
gradient card (`.hero` in src/dashboard.py: --surf -> --surf2 -> a violet
tint). Dropped in unmodified the image renders as a hard white rectangle on a
grey-lavender card.

So the white has to become transparency. Two details make that non-trivial and
are the reason this is a script rather than a one-off shell pipeline:

1. The matte is not pure white. The master's corner pixel measures (253,253,253)
   and only 61% of the frame is exactly #ffffff. A ==white test would leave a
   speckled fringe.

2. The robot has a soft grey drop shadow. A binary threshold either keeps it as
   an opaque grey blob or clips it into a hard ring. Neither looks like a
   shadow. Un-premultiplying against the known white matte turns it into what
   it actually is: low-alpha grey that composites correctly over anything.

Recording the numbers here rather than in a comment beside the asset is
deliberate. A comment claiming "keyed at threshold 200, encoded q82" cannot be
checked; this can be re-run.

Requires `cwebp` (brew install webp) at BUILD time only. The runtime path never
touches it -- src/dashboard.py reads the finished .webp and base64-inlines it.
Everything else here is stdlib, matching project convention.
"""
import os
import struct
import subprocess
import sys
import zlib
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "src", "assets", "repete_swing_master.png")
OUT = os.path.join(ROOT, "src", "assets", "repete_swing.webp")

# --- keying parameters, all measured against the master (see module docstring)
MATTE_MIN = 200      # a background pixel is at least this bright...
MATTE_SAT = 14       # ...and this close to neutral grey (max-min channel)
# Alpha at or below this snaps to fully transparent. 8/255 is ~3% opacity --
# invisible on any background, but NOT zero, and a stray 3% pixel still counts
# as content when trimming. The first build ran with 3 and the corner assertion
# below fired on an alpha-4 speckle in the master's top-left, which is exactly
# the "the matte is not pure white" problem this constant exists to absorb.
ALPHA_FLOOR = 8
PAD = 8              # px of transparent margin kept after trimming
# Final width, chosen as exactly 2x the 240 CSS px the hero renders it at, so
# it is crisp on retina and carries not one pixel more. Never upscale: the
# first build asked for 720 against a 669 px crop and cwebp happily enlarged
# it, adding 18 KB of invented detail.
WIDTH = 480
QUALITY = 80
# Measured on this image: alpha_q 60 saves ~20% but this alpha is a soft drop
# shadow, not a hard cutout, and lossy alpha is exactly where that fringes.
# 90 is the point where the shadow still reads as a shadow.
ALPHA_QUALITY = 90


def _decode_png(path):
    """Minimal PNG reader: 8-bit RGB/RGBA, non-interlaced. Returns (W,H,bpp,rows).

    Pillow is not a project dependency and this does not justify adding one.
    """
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"not a PNG: {path}")
    i, idat, W = 8, b"", None
    while i < len(data):
        ln, typ = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            W, H, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                sys.exit(f"unsupported PNG: depth={depth} colour={ctype} "
                         f"interlace={interlace}")
        elif typ == b"IDAT":
            idat += body
        i += 12 + ln
    if W is None:
        sys.exit("PNG has no IHDR")

    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = W * bpp
    rows, prev, o = [], bytearray(stride), 0
    for _ in range(H):
        f = raw[o]
        line = bytearray(raw[o + 1:o + 1 + stride])
        o += 1 + stride
        # Undo the per-scanline filter (PNG spec 9.2). Byte-at-a-time is fine
        # for a one-off build of a single 1 MP image.
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        rows.append(line)
        prev = line
    return W, H, bpp, rows


def _encode_png(path, W, H, rows):
    """RGBA out, so cwebp gets a real alpha channel to encode."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    open(path, "wb").write(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))


def _background_mask(W, H, bpp, rows):
    """Flood fill inward from the border.

    Connectivity matters: a plain "is it light?" test would also punch holes in
    light interior detail. Only matte REACHABLE FROM THE EDGE is background.
    """
    def is_matte(x, y):
        r, g, b = rows[y][x * bpp:x * bpp + 3]
        return min(r, g, b) >= MATTE_MIN and (max(r, g, b) - min(r, g, b)) <= MATTE_SAT

    seen = bytearray(W * H)
    q = deque()

    def seed(x, y):
        if not seen[y * W + x] and is_matte(x, y):
            seen[y * W + x] = 1
            q.append((x, y))

    for x in range(W):
        seed(x, 0)
        seed(x, H - 1)
    for y in range(H):
        seed(0, y)
        seed(W - 1, y)
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < W and 0 <= ny < H:
                seed(nx, ny)
    return seen


def _key(W, H, bpp, rows, seen):
    """Un-premultiply the background-connected pixels against a white matte.

    observed = C*a + 255*(1-a)  =>  a = 255-max(rgb),  C = (observed-255(1-a))/a

    Fully-white pixels give a=0 and vanish; the drop shadow gives a small a and
    survives as the soft grey it is meant to be.
    """
    out = []
    for y in range(H):
        row = bytearray()
        for x in range(W):
            r, g, b = rows[y][x * bpp:x * bpp + 3]
            if not seen[y * W + x]:
                row += bytes((r, g, b, 255))
                continue
            a = 255 - max(r, g, b)
            if a <= ALPHA_FLOOR:
                row += b"\xff\xff\xff\x00"
                continue
            row += bytes(
                tuple(max(0, min(255, round((c - 255 * (255 - a) / 255) * 255 / a)))
                      for c in (r, g, b)) + (a,))
        out.append(row)
    return out


def _trim(W, H, rows):
    """Crop to the visible content plus PAD.

    The master carries ~105 px of dead right margin and ~160 px of dead bottom.
    Left in, they shrink the robot for a given CSS box and shift him off-centre.
    """
    x0, y0, x1, y1 = W, H, -1, -1
    for y in range(H):
        r = rows[y]
        for x in range(W):
            if r[x * 4 + 3]:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        sys.exit("keying removed the entire image")
    x0, y0 = max(0, x0 - PAD), max(0, y0 - PAD)
    x1, y1 = min(W - 1, x1 + PAD), min(H - 1, y1 + PAD)
    cropped = [rows[y][x0 * 4:(x1 + 1) * 4] for y in range(y0, y1 + 1)]
    return x1 - x0 + 1, y1 - y0 + 1, cropped


def main():
    if not os.path.exists(MASTER):
        sys.exit(f"missing master: {MASTER}")
    W, H, bpp, rows = _decode_png(MASTER)
    print(f"master      {W}x{H} ({bpp * 8}bpp)")

    seen = _background_mask(W, H, bpp, rows)
    keyed = _key(W, H, bpp, rows, seen)
    cw, ch, cropped = _trim(W, H, keyed)
    print(f"trimmed     {cw}x{ch}")

    total = cw * ch
    clear = sum(1 for r in cropped for x in range(cw) if r[x * 4 + 3] == 0)
    solid = sum(1 for r in cropped for x in range(cw) if r[x * 4 + 3] == 255)
    print(f"alpha       {100 * clear / total:.1f}% clear, "
          f"{100 * solid / total:.1f}% opaque, "
          f"{100 * (total - clear - solid) / total:.1f}% partial")
    for name, (x, y) in [("TL", (0, 0)), ("TR", (cw - 1, 0)),
                         ("BL", (0, ch - 1)), ("BR", (cw - 1, ch - 1))]:
        a = cropped[y][x * 4 + 3]
        if a:
            sys.exit(f"corner {name} is not transparent (alpha={a}) -- the "
                     f"flood fill did not reach it")

    tmp = os.path.join(ROOT, "src", "assets", ".keyed.tmp.png")
    _encode_png(tmp, cw, ch, cropped)
    argv = ["cwebp", "-quiet", "-q", str(QUALITY),
            "-alpha_q", str(ALPHA_QUALITY), "-m", "6", "-alpha_filter", "best"]
    if cw > WIDTH:
        argv += ["-resize", str(WIDTH), "0"]
    else:
        print(f"note        crop is {cw}px wide, already <= {WIDTH} -- no resize")
    try:
        subprocess.run(argv + [tmp, "-o", OUT], check=True)
    except FileNotFoundError:
        sys.exit("cwebp not found -- brew install webp (build-time only)")
    finally:
        os.path.exists(tmp) and os.unlink(tmp)

    print(f"wrote       {OUT}  {os.path.getsize(OUT):,} bytes "
          f"(~{os.path.getsize(OUT) * 137 // 100:,} as base64)")


if __name__ == "__main__":
    main()
