# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""3A control loops: auto exposure, auto white balance, auto focus.

These run in **software, at frame rate**. None of this is hardware, and none of
it should become hardware. The loops read the statistics block's per-zone
accumulators once per frame, compute new sensor and pipeline register values,
and write them. A frame is 16 to 33 milliseconds; a few hundred integer
operations over a 16x16 grid is nothing next to that, and putting a control loop
in gateware trades all of its tunability for performance nobody needed.

What *is* hardware is the statistics block that feeds them, because summing every
pixel of every frame at 200 megapixels per second is not something software can
do at all.

Integer arithmetic throughout, in Q8.8 where a fraction is needed. Not because
of rule 1 -- that governs `revela/blocks/`, and these are not blocks -- but
because the values these loops produce are register codes, the sensor
descriptions define their conversions in integers, and a float intermediate
buys nothing except a rounding difference between the host that computed a
value and the log that recorded it.

Loop safety
-----------

Every loop here is damped and rate-limited. That is not caution for its own
sake: exposure and white balance both sit in feedback with the statistics they
read, at a delay of one to three frames depending on how far ahead the sensor
has committed its next frame. An undamped loop with a delay oscillates, and
oscillating exposure is far more objectionable to a viewer than exposure that is
slightly wrong and steady.
"""
from __future__ import annotations

# Q8.8 throughout: 256 is unity.
Q8 = 256


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def damp(current: int, target: int, numerator: int, denominator: int) -> int:
    """Move ``current`` a fraction ``numerator/denominator`` toward ``target``.

    A first-order filter, in integers. The fraction is the loop's damping: 1/4
    converges in about a dozen frames and does not visibly oscillate against the
    two-to-three frame delay between changing a sensor register and seeing its
    effect in the statistics.
    """
    return int(current) + (int(target) - int(current)) * int(numerator) // int(denominator)
