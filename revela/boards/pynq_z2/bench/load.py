# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Program the bitstream, report once, exit.

The board is a bitstream: power-on defaults select the ISP and
enable the direct picture, the raster genlocks itself, the link
recovers itself. This script's whole job is the one thing a volatile
FPGA cannot do alone -- getting the bitstream into the fabric after
power-on -- and then getting out of the way. Run it from a boot
oneshot (contrib/load-bitstream.service); nothing stays resident.

    python3 load.py --bit rx.bit
"""
import argparse
import sys

from pynq import Overlay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit")
    args = parser.parse_args()
    Overlay(args.bit, download=True)
    print(f"loaded {args.bit}; the fabric owns the picture from here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
