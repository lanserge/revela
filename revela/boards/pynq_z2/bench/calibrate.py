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

    python3 calibrate.py --bit rx.bit \
        --map revela_isp_core_regmap.json --values profile.json
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
    parser.add_argument("--values", default="revela_isp.defaults.json")
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
    for key, val in json.loads(Path(args.values).read_text()).items():
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
