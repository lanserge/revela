# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Black level correction: remove the sensor's pedestal, per CFA colour.

A sensor does not read zero in the dark. It reads a deliberate positive pedestal,
so that read noise stays representable instead of being clipped away at zero, and
that pedestal differs between the four CFA colours -- R, Gr, Gb and B sit in
different columns of the readout and see different dark current and different
analogue paths. Removing one average pedestal from all four leaves a residual
that demosaic turns into a coloured cast in the shadows, so this block carries
four offsets and picks between them by pixel position.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    out = clip(pixel + offset[colour], 0, 2**bit_depth - 1)

The register holds a SIGNED OFFSET THAT IS ADDED, not a pedestal that is
subtracted. That is the hardware's choice, not a translation convenience: an
adder with a signed operand is one adder, whereas a subtract-with-clamp that must
also handle a negative pedestal is an adder plus a sign fixup. Software writes
``-pedestal``; :func:`from_sensor` does that conversion once.

Saturation is to zero at the bottom because a pixel darker than its pedestal is
noise below black and there is nothing below zero to represent it, and to full
scale at the top because the offset may be positive.

Which offset applies to which pixel
-----------------------------------

``offset`` is indexed BY COLOUR, not by raw position::

    offset[0, 0] = R      offset[0, 1] = Gr
    offset[1, 0] = Gb     offset[1, 1] = B

The model takes the CFA phase as two one-bit values -- which rows R sits on, and
which columns -- and uses them as the START of stride-2 slices. Since those
starts are registers, changing sensor orientation is a two-bit write rather than
a rebuild: ONE bitstream serves every CFA order.

How this is built
-----------------

Entirely by tracing :func:`blacklevel` with np2hw. There is no hand-written
Verilog and no per-block generator: the phase select, the coefficient mux and the
datapath all fall out of the model.

np2hw does not take the slicing literally. The four planes are disjoint and
together cover every pixel, so it lowers them to ONE full-rate datapath whose
offset is selected by the pixel's position -- an adder and a 4:1 mux -- rather
than four quarter-rate paths and a recombiner.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import ContextBit, StreamPort, ispblock
from revela.params import Param

# Wide enough to hold a pedestal for any sensor revela targets (a 12-bit part
# pedestals around 64, a 16-bit part around 4096) with headroom to push black
# deliberately negative during calibration.
OFFSET_BITS = 16


@ispblock(
    version=(1, 0),
    description="Per-CFA-colour black level offset with saturation.",
    inputs=(StreamPort("in",
                       "Raw Bayer, still sitting on the sensor's pedestal."),),
    outputs=(StreamPort("out",
                        "Pedestal removed, saturated to the datapath range."),),
    consumes=("bayer_phase",),
    context=(
        ContextBit("phase_row", "bayer_phase", bit=1,
                   description="Row parity R sits on, from the pipeline's "
                               "bayer_phase context register. A register, not a "
                               "build option, so one bitstream serves every CFA "
                               "order"),
        ContextBit("phase_col", "bayer_phase", bit=0,
                   description="Column parity R sits on, from bayer_phase"),
    ),
    params=[
        Param(
            name="offset",
            bits=OFFSET_BITS,
            configurable=("bits",),
            signed=True,
            shape=(2, 2),
            labels=(("R", "Gr"), ("Gb", "B")),
            default=0,
            description=(
                "Signed offset ADDED to the pixel before saturation, indexed by CFA "
                "colour [R, Gr; Gb, B]. Write the negated sensor pedestal. Reset 0 is "
                "a pass-through, so an unconfigured pipeline shows a raised black "
                "rather than a wrong one"
            ),
        ),
    ],
)
def blacklevel(pixel, p, ctx, bit_depth: int):
    """THE model. Four CFA planes, each offset by its own colour's register.

    Written the way the hardware reads the frame: a stride-2 slice is one CFA
    plane, and the slice START is a register, so which physical half a plane
    refers to is programmable.

    This is ordinary NumPy -- run it on an array with integer phases and it does
    exactly what it says -- and it is also what np2hw traces. There is no second
    description of this block anywhere.

    The accumulator is ``int32``: a 12-bit unsigned pixel plus a 16-bit signed
    offset needs 17 bits, and ``int32`` is the narrowest NumPy integer that holds
    it. np2hw sizes the real adder from the range, so the generated accumulator
    is the width the values actually need, not 32.
    """
    value = pixel.astype(np.int32)
    out = np.empty_like(value)
    top = (1 << bit_depth) - 1
    for i, rows in enumerate((ctx.phase_row, 1 - ctx.phase_row)):
        for j, cols in enumerate((ctx.phase_col, 1 - ctx.phase_col)):
            # (i, j) indexes the COLOUR: [R, Gr; Gb, B]. The phase decides which
            # positions that colour occupies.
            out[rows::2, cols::2] = (value[rows::2, cols::2]
                                     + p.offset[i, j]).clip(0, top)
    return out.astype(np.uint16)



@blacklevel.sensor_values
def from_sensor(sensor: dict, mode_name: str | None = None) -> dict[str, int]:
    """Register values this sensor implies. The hook :mod:`revela.profiles` calls.

    Black level does not vary by readout mode, so ``mode_name`` is accepted and
    ignored -- the uniform signature is what lets the profile machinery derive
    values without knowing which blocks take what.
    """
    return offsets_from_sensor(sensor)


def offsets_from_sensor(sensor: dict) -> dict[str, int]:
    """Register values that remove ``sensor``'s pedestal, keyed by register name.

    The sensor description records the pedestal as a positive number, because
    that is what the datasheet states; the register holds the offset that is
    added. Negation happens here, once, so that no model and no host caller has
    to remember which way round it goes.

    A per-colour pedestal is used when the description gives one; otherwise the
    single model-level pedestal applies to all four colours.
    """
    black = sensor["black_level"]
    pedestal = black["pedestal"]
    per_colour = black.get("per_colour")
    declared = blacklevel.params.declaration("offset")
    grid = np.zeros(declared.shape, dtype=np.int64)
    for idx in declared.indices():
        # Which colour sits at which (i, j) is the declaration's own labels
        # -- the same [R, Gr; Gb, B] the register map documents -- so the
        # sensor schema's lower-case keys map through them, not through a
        # second table here.
        name = declared.label_of(idx).lower()
        level = per_colour[name] if per_colour else pedestal
        grid[idx] = -int(level)
    return declared.values(grid)
