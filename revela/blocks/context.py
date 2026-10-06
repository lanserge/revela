# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The pipeline context revela's blocks may consume, and the CFA orders.

Width, height, the active window, the Bayer phase, the bit depth, the frame
phase: facts about the pipeline, not about any one block, declared here
once. A block names the ones it reads in ``consumes``; the ``pipe`` block
owns the ones that are registers, and a design takes the rest from the
stream. revela's block decorator binds this vocabulary to every block it
declares.
"""
from __future__ import annotations

from np2hw.blocks.params import Context

# Bayer phase encodes the position of the R pixel within the 2x2 CFA tile:
# bit 1 is R's row parity, bit 0 is R's column parity. The four combinations are
# the four standard CFA orders.
BAYER_RGGB = 0b00
BAYER_GRBG = 0b01
BAYER_GBRG = 0b10
BAYER_BGGR = 0b11

BAYER_ORDER_NAMES = {
    BAYER_RGGB: "RGGB",
    BAYER_GRBG: "GRBG",
    BAYER_GBRG: "GBRG",
    BAYER_BGGR: "BGGR",
}
BAYER_ORDER_CODES = {name: code for code, name in BAYER_ORDER_NAMES.items()}

# The context signals every pipeline carries. Adding one here makes it available
# to any block that names it in `consumes`; it does not cost the blocks that do
# not.
CONTEXT = (
    Context(
        name="width",
        bits=16,
        default=1920,
        description="Active line length in pixels. Blocks that buffer lines are built "
                    "for a maximum; this is the length actually in use",
        follows="width",
    ),
    Context(
        name="height",
        bits=16,
        default=1080,
        description="Active frame height in lines",
        follows="height",
    ),
    Context(
        name="window_x0",
        bits=16,
        default=0,
        description="Left edge of the active window, in pixels from the line start",
        origin="register",
    ),
    Context(
        name="window_y0",
        bits=16,
        default=0,
        description="Top edge of the active window, in lines from the frame start",
        origin="register",
    ),
    Context(
        name="window_x1",
        bits=16,
        default=1920,
        description="Right edge of the active window, exclusive",
        origin="register",
        follows="width",
    ),
    Context(
        name="window_y1",
        bits=16,
        default=1080,
        description="Bottom edge of the active window, exclusive",
        origin="register",
        follows="height",
    ),
    Context(
        name="bayer_phase",
        bits=2,
        default=BAYER_RGGB,
        description="Position of the R pixel in the 2x2 CFA tile: bit 1 row parity, "
                    "bit 0 column parity. 0=RGGB 1=GRBG 2=GBRG 3=BGGR. A register, not "
                    "a build option, so one bitstream serves every sensor orientation",
        # a CFA-indexed block has no consistent phase on an odd frame
        even_frame=True,
    ),
    Context(
        name="bit_depth",
        bits=5,
        default=12,
        description="Bits per component actually delivered by the sensor. The datapath "
                    "is BUILT for a maximum; this reports what is in use, so software "
                    "can scale without a rebuild",
        follows="bit_depth",
    ),
    Context(
        name="frame_phase",
        bits=4,
        default=0,
        description="The live frame's position in the sensor's capture loop -- a "
                    "time-of-flight reference phase, an HDR exposure slot -- the "
                    "temporal twin of bayer_phase. Unlike bayer_phase it advances "
                    "every frame, so software never writes it: the receiver derives "
                    "it as (frame counter + offset) mod loop length, the loop length "
                    "and offset being the sensor's, set once. A block reads the "
                    "position of the frame i back as (frame_phase - i) mod length",
        origin="stream",
    ),
)


CONTEXT_BY_NAME = {ctx.name: ctx for ctx in CONTEXT}


def context_names() -> tuple[str, ...]:
    """Every context signal a block may name in ``consumes``."""
    return tuple(ctx.name for ctx in CONTEXT)


def resolve(name: str) -> Context:
    """The declaration for one context signal, or a useful error."""
    try:
        return CONTEXT_BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"no pipeline context signal {name!r}; the pipe block provides "
            f"{list(CONTEXT_BY_NAME)}") from None


def bayer_phase_of(order: str) -> int:
    """Register value for a CFA order named the way datasheets name it."""
    key = order.upper()
    try:
        return BAYER_ORDER_CODES[key]
    except KeyError:
        raise KeyError(
            f"unknown CFA order {order!r}; expected one of "
            f"{sorted(BAYER_ORDER_CODES)}") from None
