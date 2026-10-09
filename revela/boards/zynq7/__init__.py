# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Blocks for any Zynq-7000 board: the vendor IP, the processing system,
and the bus -- the family layer under a board's own library."""


def fabric_clocks(design) -> dict:
    """``{"fclk<i>_mhz": frequency}`` for each fabric clock the processing
    system drives in `design`, as its settings state them
    (PCW_FPGA<i>_PERIPHERAL_FREQMHZ) -- what a host sets the processor's
    clock generators to before it programs the fabric. A clock whose port
    is off (PCW_EN_CLK<i>_PORT 0) is not listed."""
    ps = [inst for inst in design.instances.values()
          if inst.ref == "revela.boards.zynq7.ps7:ps7"]
    if len(ps) != 1:
        raise ValueError(f"{design.ref}: {len(ps)} processing systems; "
                         "fabric clocks are one processor's")
    config = ps[0].config
    out = {}
    for i in range(4):
        freq = config.get(f"PCW_FPGA{i}_PERIPHERAL_FREQMHZ")
        if freq is None or str(config.get(f"PCW_EN_CLK{i}_PORT",
                                          "1")) == "0":
            continue
        out[f"fclk{i}_mhz"] = float(freq)
    return out
