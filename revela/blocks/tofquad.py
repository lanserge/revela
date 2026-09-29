# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Time-of-flight phase and amplitude, from the last four frames.

A time-of-flight sensor does not measure distance. It measures how strongly the
returning light correlates with four reference phases of the emitter's
modulation -- one reference per frame -- and distance is an arctangent of two
differences of those four. This block does that arctangent. What the angle is
worth in millimetres belongs to the sensor and lives in
:mod:`revela.blocks.tofrange`.

The input is one stream's recent history
----------------------------------------

The sensor delivers one reference phase per frame. A frame history upstream
hands this block the current frame and the three before it, and with each one
the TAG saying which phase it was captured at:

    pixel[..., k]        the sample from frame n - k   (k = 0 is the live frame)
    pixel[..., 4 + k]    that frame's phase, 0 .. 3

Four frames of ONE stream at ONE width, so a time axis on one input rather than
four inputs -- and the block is pointwise: no line buffers, nothing held here
at all. A frame's age lives in its index; the memory is the history's
business, and so is knowing what each frame was.

The samples are signed correlations riding unsigned, offset by a constant the
link adds. Every quantity below is a difference of two samples, so the offset
cancels exactly and nothing here needs to know its value.

Which frame carries which phase
-------------------------------

The index says how OLD a frame is; the tag says which phase it carries. The
tags come from the history's tag table, one entry per position in the sensor's
capture loop, so a sensor that steps its phases in any order -- 0, 1, 2, 3 or
0, 2, 1, 3 -- is a table, not a different block. This block finds, for each
phase j, the frame whose tag is j, so it assumes nothing about the order: the
four frames in the window must simply carry the four phases between them. It
routes them onto the quadrature by their tags, so EVERY
frame yields a measurement -- from the four most recent phases, whichever one
arrived last -- and the output runs at the sensor's full frame rate. Measured on
a static scene, the three windows that straddle a measurement boundary agree
with the aligned one to 0.26-0.35 mm in the mean, with the same noise. What
straddling costs is temporal: a moving target is seen across four instants, as a
sequential-exposure HDR sees it. Keeping only aligned windows is a frame gate
downstream, and a choice of rate against coherence.

The routing is on the INPUTS, not a correction added to the output angle.
Relabelling by one phase is exactly a quarter turn of the vector, so adding
quarter turns to the angle would be equivalent in exact arithmetic -- but the
CORDIC's rounding depends on where the vector starts, the two would differ in
the last bits, and every calibration and noise figure for this sensor was
measured on the arithmetic below. Selecting each phase's sample by its tag
keeps it bit-exact with that history.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

Quadrature first, which is two subtractions::

    I = p0 - p2
    Q = p1 - p3

Then one CORDIC in vectoring mode, which rotates ``(I, Q)`` onto the positive X
axis and accumulates the angle it turned through. It yields BOTH outputs from
the same iterations -- the residual X is the vector's magnitude (scaled by the
CORDIC gain) and the accumulated angle is its argument -- so the amplitude costs
nothing beyond the phase that was needed anyway. Amplitude is the confidence
signal, and a confidence that came free is one nobody is tempted to skip.

``arctan2`` of two signed values is a function of two variables, so a direct
table is two-dimensional and absurd, and reducing it to one variable needs a
divide that costs more than the whole CORDIC. What CORDIC costs instead is
``ITERATIONS`` rounds of compare, shift and add plus a table of ``ITERATIONS``
constants -- no multiplier, no divider, nothing transcendental.

Pre-scaling is not optional
---------------------------

Each round shifts by ``i`` places, so a small operand loses its bits to those
shifts and the rotation stops converging. Measured on real captures, without
pre-scaling the median angle error is 39 LSB and the 99th percentile 801 -- and
every one of the worst pixels is a low-amplitude one, where the sensor is already
struggling. Shifting ``(I, Q)`` left until the larger fills ``NORM_BITS`` first
takes the median to 3 LSB and the 99th to 5.4, which on this sensor's range is
0.19 mm and 0.34 mm against a per-pixel noise floor of 78 mm. The angle is
invariant under a common scale on both operands, so the shift costs a priority
encoder and a barrel shifter and changes nothing else.

Units
-----

The angle is a PHASE ACCUMULATOR, not radians: ``1 << PHASE_BITS`` is one full
turn. Radians would mean a scale factor here and another downstream, and a
power-of-two turn wraps for free, which is exactly the modulo a phase needs.

Written the way the hardware is built
-------------------------------------

The model is ordinary NumPy -- run it on real frames and it returns phase and
amplitude -- and it is also what np2hw traces, so every construct is one the
tracer lowers: matching a tag is a pair of comparisons multiplied
(``_is``, since the tracer compares only by order), the variable shifts are
log shifters of fixed shifts (``_shift_left``, ``_shift_right``), the bit
length is a comparison ladder. Each is what the gates are, written out.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import StreamPort, ispblock

#: Rounds of CORDIC. Each adds about one bit of angle; 12 puts the error two
#: orders of magnitude below this sensor's noise.
ITERATIONS = 12

#: Fractional bits of one full turn. 16 makes the angle a u16 that wraps on its
#: own, which is the modulo the phase wants anyway.
PHASE_BITS = 16

#: Where the pre-shift aims the larger operand. Below the accumulator's width by
#: enough that the CORDIC gain (about 1.647) cannot overflow it.
NORM_BITS = 20

#: Stages of the barrel shifter: shifts up to ``2**SHIFT_STAGES - 1`` places,
#: which must cover NORM_BITS.
SHIFT_STAGES = 5

#: Phases per measurement, and so frames in the window.
PHASES = 4

#: Both output channels are 16-bit fields: the angle is a whole u16 turn, and
#: the amplitude is carried at the same width.
MASK = (1 << 16) - 1

#: ``arctan(2**-i)`` in turns, scaled to PHASE_BITS -- BAKED, not computed:
#: ``round(atan2(1, 2**i) / (2 * pi) * 2**16)``. Integer literals, so the model
#: holds no transcendental and np2hw sees constants. The derivation is checked
#: by the test suite, where floating point is allowed.
ATAN = [8192, 4836, 2555, 1297, 651, 326, 163, 81, 41, 20, 10, 5]


@ispblock(
    version=(1, 0),
    description="Last four ToF frames to phase and amplitude, by CORDIC.",
    inputs=(StreamPort("in",
                       "A frame history's word: [k] is the sample from frame "
                       "n-k, [0] the live frame, and [4+k] that frame's "
                       "phase tag. Signed correlation samples riding "
                       "unsigned under a common offset."),),
    outputs=(StreamPort("out",
                        "Two channels: [0] phase as an accumulator where "
                        "1 << 16 is one turn, [1] amplitude. One word, "
                        "because one CORDIC produced both."),),
    params=[],
)
def tofquad(pixel, p, ctx, bit_depth: int):
    """THE model. Route by tag, quadrature, pre-scale, vectoring CORDIC."""
    s = pixel.astype(np.int64)
    sample = [s[..., k] for k in range(PHASES)]
    tag = [s[..., PHASES + k] for k in range(PHASES)]
    zero = sample[0] * 0

    # p[j]: the sample from whichever frame carries phase j.
    p_ = []
    for j in range(PHASES):
        picked = zero
        for k in range(PHASES):
            picked = picked + _is(tag[k], j, zero) * sample[k]
        p_.append(picked)

    x = p_[0] - p_[2]                      # I
    y = p_[1] - p_[3]                      # Q

    # Pre-scale: without it the shifts below starve on small operands, and the
    # pixels that starve are exactly the ones already short of light.
    big = np.where(np.abs(x) > np.abs(y), np.abs(x), np.abs(y))
    shift = (zero + NORM_BITS) - _bit_length(big, zero)
    shift = np.where(shift > 0, shift, zero)
    x = _shift_left(x, shift)
    y = _shift_left(y, shift)

    # CORDIC converges only within about +-99 degrees of the X axis, so fold the
    # left half-plane across the origin and start the angle half a turn on.
    turn = 1 << PHASE_BITS
    left = x < 0
    x = np.where(left, -x, x)
    y = np.where(left, -y, y)
    angle = np.where(left, zero + turn // 2, zero)

    for i in range(ITERATIONS):
        # Drive y towards zero; every rotation is a shift and an add.
        down = y >= 0
        xi = x >> i
        yi = y >> i
        x, y = (np.where(down, x + yi, x - yi),
                np.where(down, y - xi, y + xi))
        angle = np.where(down, angle + ATAN[i], angle - ATAN[i])

    # x is now the magnitude times the CORDIC gain. Undo the pre-scale on the
    # magnitude only: the angle never saw it.
    amp = _shift_right(x, shift)
    return np.stack([angle & MASK, amp & MASK], axis=-1)


def _is(value, constant, zero):
    """1 where ``value`` equals ``constant``, else 0 -- an equality comparator.

    Written as two ordered comparisons multiplied, because the tracer
    compares by order (``<`` ``<=`` ``>`` ``>=``) and not by equality; the
    product is the same gate.
    """
    d = value - constant
    one = zero + 1
    return np.where(d >= 0, one, zero) * np.where(d <= 0, one, zero)


def _bit_length(v, zero):
    """Position of the highest set bit, for values >= 0. A priority encoder.

    A comparison ladder rather than ``np.log2`` because it must be exact and
    integer: a float log rounds the wrong way at the powers of two, which is
    precisely where a shift amount must not be off by one.
    """
    n = zero
    for bit in range(NORM_BITS + 1):
        n = np.where((v >> bit) > 0, zero + (bit + 1), n)
    return n


def _shift_left(v, amount):
    """``v << amount`` for a per-pixel amount: a log shifter of fixed shifts."""
    for stage in range(SHIFT_STAGES):
        v = np.where(((amount >> stage) & 1) > 0, v * (1 << (1 << stage)), v)
    return v


def _shift_right(v, amount):
    """``v >> amount`` for a per-pixel amount: a log shifter of fixed shifts."""
    for stage in range(SHIFT_STAGES):
        v = np.where(((amount >> stage) & 1) > 0, v >> (1 << stage), v)
    return v
