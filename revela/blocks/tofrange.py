# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Phase to depth: the sensor's own numbers, and the convention a consumer wants.

:mod:`revela.blocks.tofquad` produces an angle, which is a fact about the
light. This block turns it into millimetres, which is a fact about THIS sensor
and this lens -- so everything sensor-specific is here, in registers, and the
arithmetic upstream carries none of it.

Three things happen, and each answers a question a consumer would otherwise
have to ask.

How far is one turn of phase
-----------------------------

The emitter's modulation frequency sets the unambiguous range: light returning
from beyond it wraps and is reported as something nearer. Phase is a circle,
and so is distance -- one turn is ``span`` millimetres -- so distance is the
phase measured from the point on the circle where the distance is zero::

    radial = ((phase - zero) mod 1 turn) * span >> PHASE_BITS

``zero`` is that point: the phase the fixed delay of the emitter, the sensor and
the readout path together puts at 0 mm. ``span`` is the unambiguous range in
millimetres. Both are measured, not derived: a target at known distances, and a
straight line through the results. On the sensor this was developed against,
three tape-measured points from 500 to 2150 mm fitted to within 4.5 mm, and a
line fitted to only the two nearest predicted the furthest -- never seen by the
fit -- to 21 mm.

The circle is cut at ``zero``, in the phase's own arithmetic: the difference
wraps in its PHASE_BITS, which is the modulo a turn needs, for nothing. Every
distance the sensor can tell apart, 0 up to one span, comes out as itself, and
none is negative. Cut anywhere else -- at phase 0, scaling first and adding a
delay in millimetres after -- the turn lands across zero distance, and a part
of the range comes out negative: on this sensor the top metre of its four.

Measurements scatter on both sides of the cut, though, and at the near end
that matters: a hand at the lens reads a little below zero distance, and
the turn would wrap it to the far end. ``guard`` is how far below zero a
reading may land and still be the near thing it is -- the last ``guard``
steps of the turn before ``zero`` -- and those read as 1 mm, the nearest
distance there is. Everything is decided in phase, where the turn wraps for
nothing: the reading is in the guard band exactly when adding ``guard`` to
it carries out of its 16 bits. Only then is it scaled, once, to
millimetres::

    turn   = (phase - zero) mod 1 turn
    near   = carry out of (turn + guard)
    radial = turn * span >> PHASE_BITS,  the depth 1 mm where near

so the window is 0 up to ``(1 - guard / 1 turn) * span``. ``guard`` is the
sensor's, measured: on the reference module a hand at the lens read 21 to
24 mm below zero, steadily -- bright pixels are quiet ones -- and the
default, a 32nd of a turn, is about five times that.

``span`` may be written negative, because a sensor is free to run its phase in
either direction and one of them does: the phase is then measured from ``zero``
the other way round.

Radial is not depth
-------------------

A time-of-flight sensor measures the distance light travelled, which is RADIAL
from the sensor. Every consumer that expects a depth image -- ROS's
``sensor_msgs/Image``, anything that unprojects to a point cloud -- means the
PERPENDICULAR distance to the image plane. The two differ by the angle off the
optical axis::

    depth = radial * cos(theta[u, v])

On a 64 x 50 degree field that correction reaches 18% in the corners.
Publishing radial range as a depth image gives a point cloud that is right in
the middle of the frame and wrong at the edges, which is the kind of error that
survives a demo and fails a robot.

``cos(theta)`` is a value per pixel position, and a table of them would be one
entry per pixel -- 43200 of them, which is a ROM and therefore a build. It is
not stored. It is COMPUTED, from four registers::

    a = (u - cx) * inv_fx
    b = (v - cy) * inv_fy
    cos(theta) = 1 / sqrt(1 + a*a + b*b)

``u`` and ``v`` are the raster's own counters, so the position costs nothing.
The reciprocals are the registers rather than the focal lengths themselves,
which turns two divides into two multiplies.

That leaves the inverse square root, and it is small: the argument cannot
escape ``1 .. 1.62`` on any real lens, because it IS one plus the squared
tangents of the field angles. Over so narrow a span the function is nearly
straight -- 16 knots with linear interpolation hold it to 0.55 mm at 4 m,
against a sensor noise floor of 78 mm. The same shape as
:mod:`revela.blocks.gamma`'s curve, and a fraction of its size.

The point of doing it this way is that the intrinsics become REGISTERS. One
bitstream then serves any lens and any per-unit calibration, exactly as
:mod:`revela.blocks.blacklevel`'s phase registers let one bitstream serve every
CFA order. It also lets the camera describe itself: a consumer that needs
``CameraInfo`` reads it from the device instead of from a file that can
disagree with the hardware it claims to describe.

Zero means nobody measured
--------------------------

Where too little light came back the phase is noise, and a noisy distance is
worse than no distance: it looks like a surface. Amplitude is the evidence, and
below ``amp_min`` this block emits ZERO.

Zero is not an arbitrary sentinel. It is what a depth image is expected to
carry for an invalid pixel, so a consumer needs no side channel and no special
value to learn. The amplitude stream is published alongside regardless, for
anyone who wants to threshold differently or weight by it.

The threshold has a measured floor to start from: on this sensor, pixels in the
bottom decile of amplitude carry about 840 mm of noise, against 47 mm in the
top decile -- an eighteen-fold spread driven purely by returned light. Those
pixels are not worth filtering; they are worth discarding.
"""
from __future__ import annotations

import numpy as np
from np2hw import Rom, coords

from np2hw.blocks import StreamPort
from revela.blocks import ispblock
from revela.blocks.tofquad import PHASE_BITS
from np2hw.blocks.params import Param

#: Millimetres. Wide enough for a span of several metres, signed so the phase
#: may run either way.
RANGE_BITS = 20

#: Fractional bits on cos(theta). 14 puts its quantisation at about 0.25 mm at
#: 4 m, two orders below the sensor's noise.
COS_BITS = 14

#: Width and fractional bits of the reciprocal focal lengths. 18 fractional
#: bits resolves a focal length of 191 px to better than a thousandth of a
#: pixel, which is finer than any calibration will ever justify.
INTR_BITS = 24
INTR_FRAC = 18

#: Knots on the inverse-square-root curve. The argument cannot leave 1 .. 1.62
#: on any real lens, and over that span 16 knots with linear interpolation hold
#: the result to 0.55 mm at 4 m. The table covers 1 .. 2, room for a wider lens
#: than this one.
RSQRT_KNOTS = 16
assert RSQRT_KNOTS & (RSQRT_KNOTS - 1) == 0, "segment width must be a power of two"

#: ``1/sqrt(x)`` at the knots ``x = 1 + i/16``, i = 0..16, scaled by
#: ``1 << COS_BITS`` -- BAKED, not computed: ``round(2**14 / sqrt(1 + i/16))``.
#: Integer literals, so the model holds no square root and np2hw sees
#: constants. The derivation is checked by the test suite, where floating
#: point is allowed.
RSQRT = [16384, 15895, 15447, 15035, 14654, 14301, 13972, 13665, 13377,
         13107, 12853, 12612, 12385, 12170, 11965, 11771, 11585]

#: The same table as a ROM: read at a traced index it is a memory, run in
#: NumPy it is an array lookup -- one object, both roles.
RSQRT_ROM = Rom(RSQRT, name="tofrange_rsqrt")

#: Both output channels are 16-bit fields.
MASK = (1 << 16) - 1


@ispblock(
    version=(2, 0),
    description="ToF phase to perpendicular depth in millimetres, invalid zeroed.",
    inputs=(StreamPort("in",
                       "Two channels from tofquad: [0] phase, [1] amplitude."),),
    outputs=(StreamPort("out",
                        "Two channels: [0] perpendicular distance to the image "
                        "plane in millimetres, zero where amplitude fell below "
                        "amp_min -- the convention a depth consumer already "
                        "expects -- and [1] that amplitude, passed through so "
                        "anyone may threshold or weight differently without a "
                        "second pipeline."),),
    params=[
        Param(
            name="span",
            bits=RANGE_BITS,
            signed=True,
            default=-4123,
            description=(
                "Millimetres per full turn of phase: the unambiguous range, "
                "negative where the sensor's phase runs backwards against "
                "distance. Measured from targets at known distance, never "
                "derived from a datasheet figure -- the modulation frequency a "
                "mode actually runs at has been wrong in the documentation "
                "before"
            ),
        ),
        Param(
            name="zero",
            bits=PHASE_BITS,
            default=48814,              # round(3071 * 2**16 / 4123)
            description=(
                "The phase at which the distance is zero, where 1 << 16 is "
                "one turn: where the fixed delay of emitter, sensor and "
                "readout together puts 0 mm. The circle of phase is cut "
                "there, so every distance up to one span comes out as itself"
            ),
        ),
        Param(
            name="guard",
            bits=PHASE_BITS,
            default=2048,               # a 32nd of a turn: 129 mm here
            description=(
                "How far below zero distance a reading may land and still be "
                "the near thing it is, in phase steps before zero: those read "
                "as 1 mm, the nearest distance, and the window ends this much "
                "short of one span. Measured on the sensor -- how far its near "
                "readings fall short of zero"
            ),
        ),
        Param(
            name="inv_fx",
            bits=INTR_BITS,
            default=1373,                  # round(2**18 / 190.92)
            description=(
                "Reciprocal of the horizontal focal length in pixels, scaled "
                "by 1 << 18. The RECIPROCAL is the register so the datapath "
                "multiplies where it would otherwise divide. Reset is the "
                "sensor this block was developed against; a different lens is "
                "a register write, not a rebuild"
            ),
        ),
        Param(
            name="inv_fy",
            bits=INTR_BITS,
            default=1371,                  # round(2**18 / 191.25)
            description="Reciprocal of the vertical focal length, scaled by 1 << 18.",
        ),
        Param(
            name="cx",
            bits=14, signed=True, default=120,
            description=(
                "Principal point column, in pixels. Not assumed to be the "
                "frame centre: on a real module the lens sits where it was "
                "glued, and a calibration that found it elsewhere is the one "
                "worth believing"
            ),
        ),
        Param(
            name="cy",
            bits=14, signed=True, default=90,
            description="Principal point row, in pixels.",
        ),
        Param(
            name="amp_min",
            bits=16,
            default=25,
            description=(
                "Amplitude below which depth is emitted as zero. Reset is the "
                "measured bottom decile, where noise reaches 840 mm against "
                "47 mm in the top decile; raise it for a cleaner cloud, lower "
                "it to keep dim returns and filter them downstream"
            ),
        ),
    ],
)
def tofrange(pixel, p, bit_depth: int):
    """THE model. Span and zero for the metres, intrinsics for the geometry.

    Ordinary NumPy -- run it on a phase image and it returns depth -- and also
    what np2hw traces. Nothing here is a float and nothing is transcendental:
    the only curve is a 17-entry table read and interpolated, the same shape
    as a tone curve.
    """
    s = pixel.astype(np.int64)
    ph = s[..., 0] & MASK
    # A 16-bit field masked to 16 bits: no value changes, but the tracer
    # then holds it as an expression it can subtract a register from.
    amp = s[..., 1] & MASK
    zero = ph * 0

    # The phase from the point of zero distance, in the direction the sensor
    # runs it, wrapping in its own bits: the turn cut where the distance is
    # zero. Then linear: the shift is the divide by a full turn, and it is a
    # shift because PHASE_BITS was chosen a power of two for exactly this.
    backwards = p.span < 0
    turn = np.where(backwards, p.zero - ph, ph - p.zero) & MASK
    # The guard band, the last steps before zero: a reading just short of
    # zero distance, which adding the guard carries out of the turn. It is
    # the nearest distance there is -- set below, once the depth is depth,
    # so no foreshortening rounds it to the zero that means "unmeasured".
    near = ((turn + p.guard) >> PHASE_BITS) > 0
    radial = (turn * np.where(backwards, -p.span, p.span)) >> PHASE_BITS

    # Where the pixel is: the row and column every core already counts, so
    # the position costs nothing.
    v, u = coords(pixel)

    # Tangents of the field angles. The reciprocal focal lengths are the
    # registers, so these are multiplies rather than divides.
    a = ((u - p.cx) * p.inv_fx) >> (INTR_FRAC - COS_BITS)
    b = ((v - p.cy) * p.inv_fy) >> (INTR_FRAC - COS_BITS)

    # 1 + a^2 + b^2, in COS_BITS fractional bits. Cannot leave 1 .. 1.62 on a
    # real lens, which is what makes the curve below so small.
    one = 1 << COS_BITS
    arg = one + ((a * a + b * b) >> COS_BITS)

    cos_theta = _rsqrt(arg, zero)
    depth = (radial * cos_theta) >> COS_BITS
    depth = np.where(near, zero + 1, depth)

    # Zero is the consumer's own word for "nobody measured this". The test
    # is written as a difference against zero: the tracer compares values
    # with constants, and this is the same comparison.
    depth = np.where((amp - p.amp_min) >= 0, depth, zero)
    return np.stack([depth & MASK, amp], axis=-1)


def _rsqrt(arg, zero):
    """``1/sqrt`` over ``1 .. 2``, by table and linear interpolation.

    A segment picked by the argument's high bits, two knots read from the
    ROM, one multiply and one shift -- the same arithmetic
    :mod:`revela.blocks.gamma` uses on its tone curve, on a curve a fraction
    of the size. ``arg`` is never below one (it is one plus two squares), so
    only the top of the range needs clamping; and because the segment width
    is a power of two, the divide is a shift and the remainder a mask.
    """
    one = 1 << COS_BITS
    span = one                                  # the table covers 1 .. 2
    step_bits = COS_BITS - (RSQRT_KNOTS.bit_length() - 1)   # log2(span / knots)
    step = 1 << step_bits
    x = arg - one
    x = np.where(x > span - 1, zero + (span - 1), x)
    # The mask changes no value that can occur -- x is 0 .. span-1, so seg is
    # already 0 .. KNOTS-1 -- but np2hw's range analysis does not narrow
    # through the clamp above, and a ROM index must be provably in the table.
    seg = (x >> step_bits) & (RSQRT_KNOTS - 1)
    frac = x & (step - 1)

    lo = RSQRT_ROM[seg]
    hi = RSQRT_ROM[seg + 1]
    return lo + (((hi - lo) * frac) >> step_bits)
