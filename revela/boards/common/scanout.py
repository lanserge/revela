# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The raster's register file: its mode and its placement, on the bus.

The raster itself is fixed Verilog (`hdl/raster.v`, behind `hdl/scanout.v`)
whose mode and placement arrive on registers, set by whoever sets the
camera's mode. What varies between builds is only what an unwritten
bitstream shows, so that is all this generates: an AXI4-Lite register
file, declared here, its reset values the build's mode from bayerlink's
rasters, the whole active area, centred, and the fine phase steps one
pixel of the raster's clock is.

The file is armed for a reader in another clock (np2hw's `arm`), and
resets ARMED: the raster takes the build's mode once without anyone
writing a word, and after that takes whatever software writes and arms.
"""

# The mode, then the placement, then the arm: one field a word.
MODE = ("h_total", "h_active", "hs_beg", "hs_end",
        "v_total", "v_active", "vs_beg", "vs_end")


def layout_id(fields) -> int:
    """A register file's layout as one word: the CRC-32 of its fields'
    names and widths, in order."""
    import zlib
    return zlib.crc32(repr(list(fields)).encode()) & 0xFFFFFFFF


def scanout_regs(mode: str, ps_per_pixel: int, clk_sel: int = 0) -> dict:
    """The raster's register file, reset to `mode` (``"1080p30"``) with
    `ps_per_pixel` fine phase steps a pixel and the display clock at row
    `clk_sel` of the build's clock table: a leaf a design names
    (`revela.boards.common.scanout:scanout_regs`) and wires to `scanout`
    and the clock select. Every port is published."""
    from bayerlink.rasters import raster
    from np2hw import axil_regfile
    from np2hw.regmap import Reg

    r = raster(mode)
    fields = ([(name, 16) for name in MODE]
              + [("clk_sel", 2), ("hs_pos", 1), ("vs_pos", 1), ("ps_per_px", 16),
                 ("win_x0", 16), ("win_y0", 16), ("win_w", 16),
                 ("win_h", 16), ("center", 1), ("arm", 1)])
    defaults = {name: getattr(r, name) for name in MODE}
    defaults.update(clk_sel=int(clk_sel),
                    hs_pos=int(r.hsync_positive), vs_pos=int(r.vsync_positive),
                    ps_per_px=int(ps_per_pixel),
                    win_x0=0, win_y0=0, win_w=r.h_active, win_h=r.v_active,
                    center=1, arm=1)
    # The file's ID: its layout, read back before anything is written, so
    # a map from another build is refused rather than written to the
    # wrong fields.
    regs_ = [Reg("id", 32, access="ro", value=layout_id(fields),
                 description="this file's layout: CRC-32 of its fields")]
    regs_ += [Reg(name, bits, reset=defaults[name]) for name, bits in fields]
    regs = axil_regfile(regs_, module_name="scanout_regs", addr_bits=8,
                        arm="arm")
    return {"verilog": regs["verilog"], "module": "scanout_regs",
            "interface": regs["interface"],
            "facts": {"clocks": {"s_axil": ["aclk", "aresetn"]},
                      # where software finds each field, and the word
                      # that says the file is this layout
                      "offsets": regs["offsets"],
                      "id": layout_id(fields)}}


scanout_regs.leaf = True
