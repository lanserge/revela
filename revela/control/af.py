# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Auto focus: drive a lens actuator from a focus statistic.

STUB -- the control law is sketched below, but it cannot be completed until the
statistics block produces a focus figure, and it needs a lens actuator that
revela does not currently talk to. Both are noted as prerequisites rather than
worked around.

Why it is blocked on hardware that does not exist yet
-----------------------------------------------------

Contrast-detection AF needs a per-zone measure of high-frequency energy: the
absolute sum of a bandpass filter's output over each zone. That is a
statistics-block feature -- one more accumulator per zone, fed from a small FIR
on the luma path -- and the statistics block does not generate RTL yet, because
accumulation over a region is a reduction and np2hw does not trace reductions.

Adding a focus figure to :mod:`revela.blocks.stats` is the prerequisite. Writing
this loop against a figure computed in software over a full frame would be
possible but pointless: reading a whole frame back to the host per focus step is
exactly the thing the statistics block exists to avoid.

Intended control law
--------------------

Contrast detection is a hill climb with no gradient information -- the focus
figure tells you how sharp you are, never which way to move -- so:

  1. COARSE SWEEP. Step the actuator across its range, recording the figure at
     each position. The peak's rough location falls out.
  2. FINE SEARCH. Step back through the peak in smaller increments, and fit a
     parabola through the three highest samples for sub-step precision.
  3. HYSTERESIS. Do not re-trigger until the figure falls by more than a
     threshold, or the scene changes. Without this, sensor noise alone
     re-triggers a search continuously, which is the visible "hunting" that
     makes contrast AF unpleasant to watch.

Two complications that are not optional in practice:

  BACKLASH. A voice-coil or stepper actuator does not return to the same
  physical position from both directions. The final approach must always come
  from the same side, or the fine search converges on a position that shifts
  depending on where the sweep started.

  EXPOSURE COUPLING. The focus figure scales with scene brightness, so a figure
  measured before an AE step is not comparable with one measured after. Either
  freeze AE during the search, or normalise the figure by the zone's luminance.
  Freezing is simpler and is what this will do.

Phase-detection AF, where the sensor provides it, replaces the whole search with
a direct measurement of defocus, including its sign -- but it needs the sensor's
PDAF pixel layout, which is model-level sensor data and would belong in
`sensor.json`.
"""
from __future__ import annotations


def solve(*args, **kwargs):
    """Not implemented yet. See the module docstring for the intended design."""
    raise NotImplementedError(
        "auto focus is a declared stub. It needs (a) a per-zone focus figure "
        "from revela.blocks.stats, which needs np2hw to trace reductions, and "
        "(b) a lens actuator transport in revela.host. Neither exists yet, and "
        "computing the focus figure on the host per frame would defeat the "
        "purpose of the statistics block.")
