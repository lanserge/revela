# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Generate the output tee: one ISP stream, two switchable consumers.

Off means DISCARD -- with both outputs off the pipeline keeps running,
which is what lets statistics keep computing in standby -- and
switching is frame-atomic, so neither the store nor the display ever
receives a partial frame.

    python3 -m revela.boards.pynq_z2.tee --out <build dir>
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The three flags that ride beside the pixels. tee_shim.v owns the
# LAYOUT -- {sof, last, eol, rgb} -- and this is the only other place
# that has to know what the flags cost.
FLAG_BITS = 3


def payload_bits(out: Path) -> int:
    """The tee's word: the ISP's TRACED output, plus the flags.

    Read, never written. It was written -- 33, meaning a 30-bit RGB --
    and stayed correct until the display curve began narrowing to eight
    bits a channel. Nothing failed, which is the problem: the payload
    sits in the low bits, so a 27-bit word into a 33-bit port
    zero-extends and comes back truncated to the same value. It cost six
    unused flip-flops twice over and 24 Kbit of block RAM in the grab
    FIFO, and the first ISP to trace more than 30 bits would have had
    its FLAGS cut off the top instead.
    """
    design = json.loads((HERE / "pipeline.json").read_text())
    contract = out / "hdl" / f"{design['name']}_build.json"
    if not contract.exists():
        sys.exit(f"tee.py: {contract.name} is missing -- generate the "
                 "ISP first. The width this tee carries is the ISP's traced "
                 "output, not a number this file may choose.")
    outputs = json.loads(contract.read_text())["boundary"]["outputs"]
    return int(next(iter(outputs.values()))["data_bits"]) + FLAG_BITS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path,
                        help="the build directory: reads the ISP's contract "
                             "from its hdl/ and writes the tee beside it")
    args = parser.parse_args()
    from np2hw import cdc_fifo, skid, tee
    width = payload_bits(args.out)
    out = args.out / "hdl"
    out.mkdir(parents=True, exist_ok=True)
    result = tee(width, module_name="isp_tee")
    (out / "isp_tee.v").write_text(result["verilog"] + "\n")
    # The grabber's elastic: the tee never stalls, so a tap's ready
    # must be as smooth as the stream -- the write engine's ready
    # dips for bursts, and without slack every dip would tear the
    # branch. Two lines of buffering makes the tap point always
    # ready, the same way the display's crossing FIFO does for its
    # branch. Same proven gray-pointer FIFO, both clocks tied.
    gfifo = cdc_fifo(width, addr_bits=12, module_name="grab_fifo")
    (out / "grab_fifo.v").write_text(gfifo["verilog"] + "\n")
    # The skid in front of it is what terminates the READY CONE: every
    # traced core's in_ready is combinational from its downstream, so
    # the chain passes ready sink-to-source, one gate deeper per block
    # -- boundary_report has said so since the day it was written, and
    # the tee's lockstep fork was the level that finally tipped it
    # (-0.557ns through seven blocks of ripple). One registered
    # boundary at the pipe's output ends the cone regardless of what
    # is wired downstream.
    result = skid(width, module_name="isp_skid")
    (out / "isp_skid.v").write_text(result["verilog"] + "\n")
    print(f"generated {out}/isp_tee.v + isp_skid.v + "
          f"grab_fifo.v ({width}-bit: the ISP's traced "
          f"{width - FLAG_BITS} plus {FLAG_BITS} flags, frame-atomic, "
          f"ready cone terminated)")


if __name__ == "__main__":
    main()
