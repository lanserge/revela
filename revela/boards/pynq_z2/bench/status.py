# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read the fabric's testimony, print it, exit.

A diagnostic visitor: decodes the status words the design publishes
-- link, receiver, island, scanout, genlock, stream identity -- one
line per sample. The picture does not need this script; it exists so
a person can ask questions.

    python3 status.py --bit rx.bit             # one line
    python3 status.py --bit rx.bit --count 10  # ten, 2 s apart

Every field is read by name through the build's gpio_layout.json (beside
the bitstream) and layout.py (beside this script).
"""
import argparse
import sys
import time

from addresses import Addresses
from layout import Layout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit",
                        help="matching bitstream (for the address map; "
                             "the PL is not reprogrammed); its "
                             "gpio_layout.json sits beside it")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    fields = Layout.beside(args.bit)
    regs = Addresses.beside(args.bit)
    gpio = {name: regs.mmio(name) for name in fields.gpios}
    fields.verify(gpio.__getitem__)

    def f(name, gpio_name="status_gpio"):
        return fields.read(gpio[gpio_name], gpio_name, name)

    for n in range(max(1, args.count)):
        if n:
            time.sleep(args.interval)
        # The scanout's status word is its generator's: sof-per-frame
        # [2:0], locked, armed, underflow, misalign, refused -- and on a
        # genlocked raster, glocked at bit 14.
        sc = f("scanout_status")
        print(f"lock={f('link_locked')} ovf={f('rx_overflow')} "
              f"isl[rst={f('island_reset')} inV={f('isp_in_valid')} "
              f"inR={f('isp_in_ready')} outV={f('isp_out_valid')} "
              f"shV={f('shim_in_valid')} shR={f('shim_in_ready')}] "
              f"refused={f('refused')} bits={f('hdr_bits')} | scanout "
              f"armed={(sc >> 4) & 1} under={(sc >> 5) & 1} "
              f"misalign={(sc >> 6) & 1} win_refused={(sc >> 7) & 1} "
              f"sof/frame={sc & 7} glocked={(sc >> 14) & 1} | "
              f"src={f('source_id', 'stream_gpio')} "
              f"frame={f('frame_seq', 'stream_gpio')} "
              f"link={'up' if f('link_up', 'stream_gpio') else 'DOWN'} "
              f"drops={f('loss_count', 'stream_gpio')} "
              f"resyncs={f('resync_count', 'stream_gpio')} "
              f"torn[disp={f('torn_a', 'stream_gpio')} "
              f"grab={f('torn_b', 'stream_gpio')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
