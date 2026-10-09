# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Program the bitstream, report once, exit.

The board is a bitstream: power-on defaults select the ISP and
enable the direct picture, the raster genlocks itself, the link
recovers itself. This script's whole job is the one thing a volatile
FPGA cannot do alone -- getting the bitstream into the fabric after
power-on -- and then getting out of the way. Run it from a boot
oneshot (load-bitstream.service, beside this script); nothing stays resident.

    python3 load.py --bit rx.bit

Two things happen, in this order: the processor's fabric clocks are set to
what the design states (``clocks.json``, written by the build beside the
bitstream), and the fabric is programmed. Nothing reads the hardware
handoff: the design builds its own buses, so the handoff holds only the
processor, and its clocks are the design's to state.
"""
import argparse
import json
import sys
from pathlib import Path

from pynq import Bitstream, Clocks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit")
    args = parser.parse_args()
    bit = Path(args.bit).resolve()
    clocks = json.loads((bit.parent / "clocks.json").read_text())
    # the clocks first, so the fabric never runs on the last design's
    for name, mhz in sorted(clocks.items()):
        setattr(Clocks, name, mhz)
    Bitstream(str(bit)).download()
    print(f"loaded {bit.name}; clocks "
          + ", ".join(f"{n[:-4]} {getattr(Clocks, n):.3f} MHz"
                      for n in sorted(clocks))
          + "; the fabric owns the picture from here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
