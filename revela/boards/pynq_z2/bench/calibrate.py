# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Write a calibration into the ISP's registers, once, and exit.

INTERIM TOOL, and honestly labelled: parameters belong to whoever
owns the sensor. The sensor's owner sits at the far end of the
cable, knows which sensor it is, and reaches these same registers
through the display cable's own control channel -- that is where
this job is moving. Until the sender side carries its per-sensor
profiles, this script does the write locally: the map answers
where, the profile answers what, the commit lands it at a frame
boundary.

The design's own values are the bitstream's reset; what this writes
is the sensor's. The values are ABSOLUTE keys, which this board has no
revela to compute: resolve the profile against the build's map where
revela is installed, anchored wherever its composite sits. Several
files are applied in order, a later one winning:

    python3 -m revela.designs.profile revela.sensors.ov5647.profile:ov5647 \
        --map revela_isp_core_regmap.json > sensor.json

    python3 calibrate.py --bit rx.bit --map revela_isp_core_regmap.json \
        --values sensor.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

from pynq import Overlay, MMIO


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bit", default="rx.bit")
    parser.add_argument("--map", default="revela_isp_core_regmap.json")
    parser.add_argument("--values", action="append", required=True,
                        help="absolute key -> value JSON; repeat, later "
                             "files win")
    args = parser.parse_args()

    overlay = Overlay(args.bit, download=False)
    if "isp" not in overlay.ip_dict:
        print("this bitstream has no live register file (baked build)")
        return 2
    rmap = json.loads(Path(args.map).read_text())
    base = overlay.ip_dict["isp"]["phys_addr"]
    csr = MMIO(base, 1 << rmap["control"]["address_bits"])
    addr = {f"{b['path']}.{r['name']}": (r["address"], r["bits"])
            for b in rmap["blocks"] for r in b["registers"]}
    wrote = 0
    values = {}
    for path in args.values:
        values.update(json.loads(Path(path).read_text()))
    # A key this build does not have is refused, not skipped: a values
    # file resolved against another build would otherwise write nothing
    # and report success.
    unknown = sorted(set(values) - set(addr))
    if unknown:
        print(f"{len(unknown)} key(s) this map does not have, e.g. "
              f"{unknown[:3]}; resolve the profiles against THIS build's "
              "map (see the docstring)", file=sys.stderr)
        return 2
    for key, val in values.items():
        if key in addr:
            a, bits = addr[key]
            csr.write(a, (val + (1 << bits)) % (1 << bits))
            wrote += 1
    csr.write(addr["pipe.commit"][0], 1)
    deadline = time.time() + 1.0
    while (csr.read(addr["pipe.commit"][0]) & 1) and time.time() < deadline:
        time.sleep(0.005)
    state = ("applied" if not (csr.read(addr["pipe.commit"][0]) & 1)
             else "PENDING (no frames yet; lands at the first boundary)")
    print(f"{wrote} registers written; commit {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
