# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Read the HDMI receiver's survey of its lanes, print it, exit.

tmds_rx keeps what its alignment saw: for every lane, every bit slip and
every delay tap, how many control tokens the tap misread, or that it saw
no blank at all. This collects the whole of it and draws each lane's
eyes, one row a bit slip, with where the lane samples marked -- the
margin a link has on this board, cable and source, measured.

    python3 rx_survey.py --bit rx.bit
    python3 rx_survey.py --bit rx.bit --json survey.json

In a row, `.` is a tap that saw no blank, `0` one that misread nothing,
`1`-`9` that many tokens misread, `+` more. The receiver samples a slip's
eye at the middle of the taps that see blanks at all.

The fabric presents one entry at a time, each held for ~0.1 ms; an entry
is taken when two reads in a row agree, so no read straddling a change is
believed. Every field is read by name through the build's
gpio_layout.json (beside the bitstream) and layout.py (beside this
script). Needs tmds_rx as the front end: a build without it has no rx_gpio.
"""
import argparse
import json
import sys
import time

from addresses import Addresses
from layout import Layout

GPIO = "rx_gpio"
ENTRY = ("survey_lane", "survey_slip", "survey_tap", "survey_count",
         "survey_lane_slip", "survey_lane_tap", "survey_lane_aligned")


def glyph(count):
    if count is None:
        return "?"
    if count == 255:
        return "."
    return str(count) if count < 10 else "+"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit",
                        help="matching bitstream (for the address map; "
                             "the PL is not reprogrammed); its "
                             "gpio_layout.json sits beside it")
    parser.add_argument("--json", help="write the survey here as well")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="how long to collect for at most")
    args = parser.parse_args()

    fields = Layout.beside(args.bit)
    regs = Addresses.beside(args.bit)
    if GPIO not in fields.gpios:
        print(f"{GPIO}: not in this build -- the front end is not tmds_rx",
              file=sys.stderr)
        return 1
    gpio = {name: regs.mmio(name) for name in fields.gpios}
    fields.verify(gpio.__getitem__)
    mmio = gpio[GPIO]
    offset = fields.field(GPIO, "survey_count")[0]

    status = {name: fields.read(mmio, GPIO, name)
              for name in ("bonded", "clock_locked", "aligned", "eye",
                           "slip")}
    lanes = len(status["aligned"])
    counts, where = {}, {}
    # every tap the survey's tap field counts, and a TMDS word's ten bits
    taps, slips = 1 << fields.field(GPIO, "survey_tap")[2], 10
    deadline = time.time() + args.seconds
    previous = None
    while time.time() < deadline and len(counts) < lanes * slips * taps:
        word = mmio.read(offset)
        if word != previous:
            previous = word
            continue
        e = {name: fields.decode(GPIO, name, word) for name in ENTRY}
        counts[e["survey_lane"], e["survey_slip"], e["survey_tap"]] = \
            e["survey_count"]
        where[e["survey_lane"]] = (e["survey_lane_slip"],
                                   e["survey_lane_tap"],
                                   e["survey_lane_aligned"])

    print(f"bonded={status['bonded']} clock_locked={status['clock_locked']} "
          f"| {len(counts)} of {lanes * slips * taps} entries")
    for lane in range(lanes):
        slip, tap, aligned = where.get(lane, (None, None, 0))
        print(f"lane {lane}: aligned={aligned} samples at slip {slip} tap "
              f"{tap}, eye {status['eye'][lane]} taps misreading nothing")
        for s in range(slips):
            row = "".join(glyph(counts.get((lane, s, t)))
                          for t in range(taps))
            mark = f"   <- tap {tap}" if s == slip else ""
            print(f"  slip {s}: {row}{mark}")
    if args.json:
        with open(args.json, "w") as out:
            json.dump({"status": status,
                       "where": {str(k): v for k, v in where.items()},
                       "counts": {f"{k[0]},{k[1]},{k[2]}": v
                                  for k, v in counts.items()}}, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
