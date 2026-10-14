# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read a running sensor's registers back over I2C, for its descriptor.

A sensor descriptor's register values must come from the datasheet or from
the running part -- never from a GPL driver's tables. This is the second
route: while some driver holds the sensor in a mode, read the registers
back and record what the part is actually doing. Reads only; the sensor is
never written.

Runs on the host the sensor is wired to, with the standard library alone:

    python3 readback.py --bus 10 --address 0x36 \\
        --range 0x3000-0x3fff --range 0x4000-0x4fff --range 0x5000-0x5fff \\
        > ov5647_1920x1080.json

`diff_modes` then names the registers that differ between two dumps -- the
writes that select one mode from the other.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import sys

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class _Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class _RdWr(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_Msg)), ("nmsgs", ctypes.c_uint32)]


def read_registers(bus: int, address: int, registers, address_bytes: int = 2):
    """{register: value} for each 8-bit register, by a combined
    write-address-then-read transfer -- the kernel passes those through
    whichever driver has the device bound, and they change nothing."""
    out = {}
    with open(f"/dev/i2c-{bus}", "rb+", buffering=0) as dev:
        for reg in registers:
            wbuf = (ctypes.c_uint8 * address_bytes)(
                *reg.to_bytes(address_bytes, "big"))
            rbuf = (ctypes.c_uint8 * 1)()
            msgs = (_Msg * 2)(_Msg(address, 0, address_bytes, wbuf),
                              _Msg(address, I2C_M_RD, 1, rbuf))
            fcntl.ioctl(dev, I2C_RDWR, _RdWr(msgs, 2))
            out[reg] = rbuf[0]
    return out


def diff_modes(a: dict, b: dict, volatile=()) -> dict:
    """{register: (value in a, value in b)} where two dumps differ, less
    the registers that move on their own (exposure, gain, status)."""
    skip = set()
    for lo, hi in volatile:
        skip.update(range(lo, hi + 1))
    return {r: (a[r], b[r]) for r in sorted(set(a) & set(b))
            if a[r] != b[r] and r not in skip}


def _span(text: str):
    lo, _, hi = text.partition("-")
    return range(int(lo, 0), int(hi or lo, 0) + 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bus", type=int, required=True)
    ap.add_argument("--address", type=lambda t: int(t, 0), required=True)
    ap.add_argument("--range", action="append", type=_span, required=True,
                    help="registers to read, LO-HI (inclusive), repeatable")
    args = ap.parse_args()
    regs = [r for span in args.range for r in span]
    values = read_registers(args.bus, args.address, regs)
    json.dump({f"0x{r:04x}": v for r, v in values.items()}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
