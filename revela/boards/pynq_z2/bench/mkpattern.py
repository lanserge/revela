# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Author a geometry ruler as a bayerlink recording, at the ISP's own depth.

bayerlink's built-in patterns are 12-bit. A 10-bit ISP fed one of them is
being asked a question about a depth it was not built for, and the answer
tells you about the unpacker, not about the geometry you were measuring.
So the recording is AUTHORED here, at whatever depth the pipeline under
test actually runs.

What it draws, and why each part earns its place:

  a flat mid-grey field       nothing clips, so white balance and the
                              colour matrix cannot move an edge

  vertical bars of DISTINCT   a bar tells you where it is horizontally;
  widths at known columns     distinct widths tell you WHICH bar you are
                              looking at, so a shift larger than the bar
                              spacing is still unambiguous

  horizontal bars, likewise   the same argument for the vertical axis

  every CFA phase equal       the demosaic returns grey, so a colour cast
                              cannot be mistaken for a displaced edge

The bars are 8 pixels of margin away from any multiple of 16, because the
read offset this measures is constrained to 64-byte steps: a landmark that
sat on that grid could hide a whole step of error.
"""
from __future__ import annotations

import argparse

import numpy as np


# (column, width) and (row, height). Widths are distinct so that a bar is
# self-identifying, and the spacing is far larger than any plausible shift.
V_BARS = ((104, 4), (504, 8), (904, 16), (1304, 32))
H_BARS = ((104, 4), (404, 8), (704, 16))


def ruler(width: int, height: int, bits: int) -> np.ndarray:
    """The pattern as (height, width) uint16 Bayer samples."""
    top = (1 << bits) - 1
    a = np.full((height, width), top // 2, np.uint16)   # mid grey, no clipping
    for x, w in V_BARS:
        if x + w <= width:
            a[:, x:x + w] = top
    for y, h in H_BARS:
        if y + h <= height:
            a[y:y + h, :] = top
    # A corner block that exists only at the top-left: it breaks the
    # symmetry, so a wrapped picture cannot be read as an unwrapped one.
    a[8:40, 8:40] = 0
    return a


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--bits", type=int, default=10,
                   help="MATCH THE PIPELINE: a 10-bit ISP wants 10")
    p.add_argument("--bayer", default="GBRG")
    p.add_argument("--out", default="ruler10.npy")
    args = p.parse_args()

    from bayerlink import encode_frame

    raw = ruler(args.width, args.height, args.bits)
    # The raster is STATED rather than left to bayerlink's default, so
    # this works against the RELEASED bayerlink and not only against a
    # tree with the newer default in it. A container is one line taller
    # than its picture: line 0 carries the 48-byte header.
    frame = encode_frame(raw, args.bayer, frame_seq=0,
                         display=(args.width, args.height + 1),
                         bits=args.bits)
    np.save(args.out, frame[None, ...])
    print(f"wrote {args.out}: {args.width}x{args.height} {args.bits}-bit "
          f"{args.bayer}, container {frame.shape}")
    print(f"landmarks: vertical bars at {[x for x, _ in V_BARS]}, "
          f"horizontal at {[y for y, _ in H_BARS]}, "
          "black 32x32 block at (8, 8)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
