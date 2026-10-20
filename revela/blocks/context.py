# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The pipeline context revela's blocks may consume, and the CFA orders.

Width, height, the Bayer phase, the bit depth, the frame count: facts
about the pipeline, not about any one block, declared here
once. A block takes what it needs as scalar inputs, and a design binds
each input to an expression over these (``"bayer_phase"``, ``"frame_seq &
3"``); the ``pipe`` block owns the ones that are registers, and a design
takes the rest from the stream. revela's block decorator binds this vocabulary to every block it
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
# to any binding that names it; it does not cost the designs that do not.
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
        name="bayer_phase",
        bits=2,
        default=BAYER_RGGB,
        description="Position of the R pixel in the 2x2 CFA tile: bit 1 row parity, "
                    "bit 0 column parity. 0=RGGB 1=GRBG 2=GBRG 3=BGGR. A register, not "
                    "a build option, so one bitstream serves every sensor orientation",
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
        name="frame_seq",
        bits=32,
        default=0,
        description="Which frame this is: the frame count each header carries, as "
                    "the source counts. It advances every frame, so software never "
                    "writes it. A block that follows a capture loop -- a "
                    "time-of-flight reference phase, an HDR exposure slot -- takes "
                    "its place in the loop as a scalar input, and the pipeline binds "
                    "that input to an expression over this count (frame_seq & 3 for "
                    "a source that anchors its loop of four there)",
        origin="stream",
    ),
)


CONTEXT_BY_NAME = {ctx.name: ctx for ctx in CONTEXT}


def context_names() -> tuple[str, ...]:
    """Every context signal a binding may name."""
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
