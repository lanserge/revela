# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read the fabric's testimony, print it, exit.

A diagnostic visitor: decodes the status words the design publishes
-- link, receiver, island, scanout, genlock, stream identity -- one
line per sample. The picture does not need this script; it exists so
a person can ask questions.

    python3 status.py --bit rx.bit             # one line
    python3 status.py --bit rx.bit --count 10  # ten, 2 s apart
"""
import argparse
import sys
import time

from pynq import Overlay, MMIO


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit",
                        help="matching bitstream (for the address map; "
                             "the PL is not reprogrammed)")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    overlay = Overlay(args.bit, download=False)
    gpio = MMIO(overlay.ip_dict["status_gpio"]["phys_addr"], 0x1000)
    hgpio = MMIO(overlay.ip_dict["hdr_gpio"]["phys_addr"], 0x1000)

    for n in range(max(1, args.count)):
        if n:
            time.sleep(args.interval)
        s1 = gpio.read(0)
        s2 = gpio.read(0x8)
        seq = hgpio.read(0)
        idw = hgpio.read(0x8)
        src, resyncs = idw & 0xFF, (idw >> 8) & 0xFF
        up, losses = (idw >> 16) & 1, (idw >> 17) & 0xFF
        hdr = s1 >> 18
        # scanout's status word: sof-per-frame[2:0], locked, armed,
        # underflow, misalign, refused -- and on a genlocked raster,
        # glocked at bit 14.
        sc = (s2 >> 17) & 0x7FFF
        isl = (s1 >> 2) & 0x3F
        torn_a, torn_b = (idw >> 25) & 1, (idw >> 26) & 1
        print(f"lock={s1 & 1} ovf={(s1 >> 1) & 1} "
              f"isl[rst={isl & 1} inV={(isl >> 1) & 1} inR={(isl >> 2) & 1} "
              f"outV={(isl >> 3) & 1} shV={(isl >> 4) & 1} "
              f"shR={(isl >> 5) & 1}] "
              f"refused={hdr & 1} bits={(hdr >> 4) & 0x1F} | scanout "
              f"armed={(sc >> 4) & 1} under={(sc >> 5) & 1} "
              f"misalign={(sc >> 6) & 1} win_refused={(sc >> 7) & 1} "
              f"sof/frame={sc & 7} glocked={(sc >> 14) & 1} | "
              f"src={src} frame={seq} link={'up' if up else 'DOWN'} "
              f"drops={losses} resyncs={resyncs} "
              f"torn[disp={torn_a} grab={torn_b}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
