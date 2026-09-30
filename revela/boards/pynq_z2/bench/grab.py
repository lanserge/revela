# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Grab frames to a file, then leave the fabric exactly as found.

The grabber is an instrument, not a path the picture depends on:
the display is direct and genlocked, and stays whole whether this
script runs, tears, or is never invoked. Each frame is captured
cleanly by construction -- arm, wait for the engine's own
completion, halt inside the following blanking, then copy -- so no
software race with the writer is possible.

Bounded by shape: N frames, then the engine is halted, the grab
branch disabled, the buffer freed, and the process exits. A write
engine must never outlive the memory it writes.

    python3 grab.py --bit rx.bit --frames 3 --out frames.npy
"""
import argparse
import sys
import time

import numpy as np
from pynq import Overlay, allocate, MMIO

MODE_W, MODE_H = 1920, 1080


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit",
                        help="matching bitstream (address map only; "
                             "the PL is not reprogrammed)")
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--out", default="frames.npy",
                        help="output: (frames, H, W, 4) uint8, xRGB -- "
                             "pixel-for-pixel what direct puts on glass")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="per-frame wait budget, seconds")
    args = parser.parse_args()

    overlay = Overlay(args.bit, download=False)
    ctrl = MMIO(overlay.ip_dict["ctrl_gpio"]["phys_addr"], 0x1000)
    vdma = MMIO(overlay.ip_dict["vdma"]["phys_addr"], 0x1000)

    fb = allocate(shape=(MODE_H + 1, MODE_W, 4), dtype="u1")
    out = np.zeros((args.frames, MODE_H, MODE_W, 4), dtype=np.uint8)
    got = 0
    try:
        for n in range(args.frames):
            # per-frame ritual, in the one order that cannot wedge:
            # arm the engine, open the branch, wait for the engine's
            # own completion, CLOSE THE BRANCH FIRST (frame-atomic --
            # the tee stops feeding at a boundary, so the engine is
            # never left mid-frame refusing beats), then halt and
            # copy a buffer nobody is writing.
            # soft-reset first: DMAIntErr latches until DMACR
            # reset, and an engine carrying yesterday's error
            # ignores today's programming
            vdma.write(0x30, 0x4)
            t0 = time.time()
            while (vdma.read(0x30) & 0x4) and time.time() - t0 < 1:
                time.sleep(0.001)
            # frame-count mode, count 1: the engine HALTS ITSELF
            # after each armed frame, so even a kill -9 that skips
            # every cleanup leaves at most one frame time of writes
            # into memory this process still owns. A write engine
            # must never outlive its buffer -- here it cannot.
            vdma.write(0x30, 0x3 | (1 << 4) | (1 << 16))
            # ALL frame-store addresses point at the one buffer: an
            # unprogrammed store register is ADDRESS ZERO, and a
            # circular engine visits every store it was built with
            # (bench-paid: two of three frames went toward low DDR
            # with DMAIntErr to show for it)
            vdma.write(0xAC, fb.physical_address)
            vdma.write(0xB0, fb.physical_address)
            vdma.write(0xB4, fb.physical_address)
            vdma.write(0xA8, MODE_W * 4)         # stride
            vdma.write(0xA4, MODE_W * 4)         # hsize
            vdma.write(0xA0, MODE_H)             # vsize -> armed
            vdma.write(0x34, 0x1000)             # W1C completion
            ctrl.write(0, ctrl.read(0) | 0x8)    # branch on
            deadline = time.time() + args.timeout
            while not (vdma.read(0x34) & 0x1000):
                if time.time() > deadline:
                    print(f"no frame within {args.timeout}s "
                          f"(source down?) -- got {got}")
                    return 2
                time.sleep(0.002)
            ctrl.write(0, ctrl.read(0) & ~0x8)   # branch off, drains
            # frame-count mode already halted the engine at the
            # frame's end; wait for its own halt to confirm
            deadline = time.time() + 1.0
            while not (vdma.read(0x34) & 1):
                if time.time() > deadline:
                    break
                time.sleep(0.001)
            fb.invalidate()
            out[n] = fb[:MODE_H]
            got = n + 1
    finally:
        ctrl.write(0, ctrl.read(0) & ~0x8)       # branch off
        vdma.write(0x30, 0x0)                    # engine halted
        time.sleep(0.05)
        fb.freebuffer()
    np.save(args.out, out[:got])
    print(f"grabbed {got} frame(s) -> {args.out} "
          f"({got}x{MODE_H}x{MODE_W}x4 uint8, xRGB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
