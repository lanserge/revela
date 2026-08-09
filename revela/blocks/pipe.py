# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The ``pipe`` block: pipeline context, owned in one place and fanned out.

Width, height, the active window, the Bayer phase and the bit depth are facts
about the pipeline, not about any one block. They are declared here, once, and
reach their consumers as WIRES. No other block holds a copy.

That is worth being firm about. The tempting alternative -- give every block its
own width register -- means a resolution change is N register writes that must
all land in the same frame, and any block whose write is missed corrupts the
image in a way that looks like a bug in that block. One owner, fanned out, makes
that class of failure impossible to express.

``pipe`` is an ordinary block. It has a ParamSet, an ID-and-version word at local
offset 0, and it gets a base address from the allocator like everything else. It
is placed at base 0 by convention, because a host bringing up an unknown
bitstream has to start reading somewhere, but nothing in the code special-cases
it: :mod:`revela.compose` allocates it through the same path as the rest.

Why these are registers and not build-time constants
----------------------------------------------------

Bayer phase is two bits. Making it a register costs almost nothing and lets ONE
bitstream serve every sensor, every flip, and every mirror setting. Generating a
different pipeline per sensor would turn the verification matrix into sensors x
modes, and a matrix like that does not stay green. Only bit depth (datapath
width) and maximum line length (line buffer sizing) are build-time, because they
are structural.
"""
from __future__ import annotations

from revela.blocks import configblock
from revela.params import Context

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
    ),
    Context(
        name="height",
        bits=16,
        default=1080,
        description="Active frame height in lines",
    ),
    Context(
        name="window_x0",
        bits=16,
        default=0,
        description="Left edge of the active window, in pixels from the line start",
    ),
    Context(
        name="window_y0",
        bits=16,
        default=0,
        description="Top edge of the active window, in lines from the frame start",
    ),
    Context(
        name="window_x1",
        bits=16,
        default=1920,
        description="Right edge of the active window, exclusive",
    ),
    Context(
        name="window_y1",
        bits=16,
        default=1080,
        description="Bottom edge of the active window, exclusive",
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
    ),
)

# No stream ports: `pipe` owns configuration and fans context out as wires. It is
# still an ordinary block -- allocated an address like any other, with an
# ID-and-version word -- it simply has no ports to wire.
pipe = configblock(
    "pipe",
    version=(1, 0),
    description="Pipeline-wide context, fanned out to blocks as wires.",
    params=[ctx.as_param() for ctx in CONTEXT],
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


@pipe.sensor_values
def from_sensor(sensor: dict, mode_name: str | None = None) -> dict[str, int]:
    """Context register values implied by a sensor description and its mode.

    Only what the sensor genuinely determines. Everything else -- the active
    window in particular -- is an application choice and is left at its default,
    for a profile to set if it has a reason to.
    """
    from revela import sensors

    mode = sensors.mode(sensor, mode_name)
    return {
        "width": int(mode["width"]),
        "height": int(mode["height"]),
        "window_x1": int(mode["width"]),
        "window_y1": int(mode["height"]),
        "bayer_phase": bayer_phase_of(sensor["cfa"]["order"]),
        "bit_depth": int(sensor["format"]["bit_depth"]),
    }
