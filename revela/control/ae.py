# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Auto exposure: choose an exposure time and gain from the luminance statistics.

Reads the statistics block's per-zone luminance, forms a single scene brightness
figure, and moves exposure and gain toward the target. Pure software, once per
frame.

Exposure before gain
--------------------

Given a required total light amount, there is always a choice between a longer
integration time and more gain. Exposure time is preferred until it runs out,
because gain amplifies read noise along with signal while exposure does not --
doubling exposure improves signal-to-noise by 3 dB, doubling gain does nothing
for it. Gain is only used once exposure has hit the ceiling imposed by the frame
rate, or by a motion-blur limit the caller supplies.

That ordering is why :func:`solve` returns both values together rather than
offering two independent controls: they are not independent, and letting a
caller set them separately means somebody eventually runs at 8x gain with a
1 ms exposure and wonders why the image is noisy.

Metering
--------

The scene figure is a weighted mean of the per-zone luminance. Centre weighting
is the default because the subject is usually near the middle and because a
uniform average is dominated by whatever occupies the most area -- typically the
sky, which is how you get correctly-exposed clouds above a silhouetted subject.

Zones that are saturated or crushed are excluded: a saturated zone's mean
understates how much light it received (everything above full scale reads as
full scale), so including it biases the loop toward *more* exposure exactly when
it needs less.
"""
from __future__ import annotations

import numpy as np

from revela import sensors
from revela.blocks import stats as stats_block
from revela.control import Q8, clamp, damp

# Fraction of full scale to aim the metered luminance at. 18% is the reflectance
# of a standard grey card, which is what "correctly exposed" conventionally
# means for an average scene.
DEFAULT_TARGET_Q8 = int(0.18 * Q8)

# Convergence rate, as a fraction applied per frame. A quarter converges in
# roughly a dozen frames without oscillating against the sensor's two-to-three
# frame command latency.
DAMPING = (1, 4)

# A zone is ignored if its mean luminance is within this fraction of the ends of
# the range, where the measurement no longer reflects the light that arrived.
SATURATION_MARGIN_Q8 = 250      # of 256
CRUSH_MARGIN_Q8 = 4


def centre_weights(zones_x: int, zones_y: int) -> np.ndarray:
    """Integer centre-weighted metering mask, one weight per zone.

    Weights fall off with distance from the centre in Chebyshev (square-ring)
    distance, which produces the concentric pattern camera meters have used for
    decades and costs a subtraction rather than a square root.
    """
    ys = np.abs(np.arange(zones_y) * 2 - (zones_y - 1))
    xs = np.abs(np.arange(zones_x) * 2 - (zones_x - 1))
    rings = np.maximum(ys[:, None], xs[None, :])
    span = max(1, int(rings.max()))
    return (8 - (rings * 7 // span)).astype(np.int64).ravel()


def scene_luminance(statistics: np.ndarray, bit_depth: int,
                    zones_x: int, zones_y: int,
                    weights: np.ndarray | None = None) -> int:
    """Metered scene luminance as a Q8.8 fraction of full scale.

    Args:
        statistics: the ``(zones, 5)`` record array from the statistics block.
        bit_depth: datapath width, to normalise against full scale.
        zones_x, zones_y: grid in use.
        weights: per-zone metering weights; centre-weighted if omitted.

    Returns:
        Q8.8 fraction of full scale, 0..256. Zero if every zone was excluded.
    """
    means = stats_block.zone_means(statistics)
    luma = means[:, stats_block.STATS_LAYOUT.index("sum_y")]
    counts = np.asarray(statistics, dtype=np.int64)[
        :, stats_block.STATS_LAYOUT.index("count")]

    full_scale = (1 << bit_depth) - 1
    normalised = luma * Q8 // full_scale

    if weights is None:
        weights = centre_weights(zones_x, zones_y)

    usable = ((counts > 0)
              & (normalised < SATURATION_MARGIN_Q8)
              & (normalised > CRUSH_MARGIN_Q8))
    if not usable.any():
        # Everything is clipped or black. Fall back to the unfiltered mean so the
        # loop still moves in the right direction instead of stalling.
        usable = counts > 0
        if not usable.any():
            return 0

    effective = np.where(usable, weights, 0)
    total = int(effective.sum())
    if total == 0:
        return 0
    return int((normalised * effective).sum() // total)


def solve(description: dict, statistics: np.ndarray, *,
          current_exposure_ns: float, current_gain_q8: int,
          bit_depth: int, zones_x: int, zones_y: int,
          target_q8: int = DEFAULT_TARGET_Q8,
          max_exposure_ns: float | None = None,
          mode_name: str | None = None) -> dict:
    """One AE iteration: statistics in, sensor register values out.

    Args:
        description: the sensor description.
        statistics: ``(zones, 5)`` records for the frame just completed.
        current_exposure_ns: exposure the measured frame was taken with.
        current_gain_q8: total gain that frame was taken with, Q8.8.
        bit_depth: datapath width.
        zones_x, zones_y: statistics grid in use.
        target_q8: metering target as a Q8.8 fraction of full scale.
        max_exposure_ns: motion-blur ceiling, if the caller has one. The frame
            rate imposes its own ceiling regardless.
        mode_name: sensor mode, if not the default.

    Returns:
        ``exposure_ns``, ``gain_q8``, and the register values to write:
        ``coarse_integration``, ``analogue_gain_code``. Also ``measured_q8`` and
        ``converged``, so a caller can log the loop rather than guess at it.
    """
    measured = scene_luminance(statistics, bit_depth, zones_x, zones_y)

    # Total light the sensor should collect, relative to what it just collected.
    # Guard the divide: a black frame gives no ratio, so hold and let the next
    # frame decide rather than dividing by zero or slamming to maximum.
    if measured <= 0:
        ratio_q8 = Q8 * 2                       # dark: open up, but only by a stop
    else:
        ratio_q8 = clamp(target_q8 * Q8 // measured, Q8 // 16, Q8 * 16)

    current_light = max(1, int(current_exposure_ns) * max(Q8, current_gain_q8))
    wanted_light = current_light * ratio_q8 // Q8

    ceiling = sensors.max_exposure_ns(description, mode_name)
    if max_exposure_ns is not None:
        ceiling = min(ceiling, float(max_exposure_ns))

    # Exposure first, gain only for what exposure cannot reach.
    exposure_ns = min(ceiling, wanted_light / Q8)
    exposure_ns = max(exposure_ns, sensors.line_time_ns(description, mode_name))
    gain_q8 = clamp(wanted_light // max(1, int(exposure_ns)), Q8,
                    sensors.max_gain_q8(description))

    # Damp both, so the loop converges rather than hunting against its own delay.
    exposure_ns = damp(int(current_exposure_ns), int(exposure_ns), *DAMPING)
    gain_q8 = damp(int(current_gain_q8), int(gain_q8), *DAMPING)

    coarse = sensors.exposure_lines(description, exposure_ns, mode_name)
    # Coarse integration is quantised to line times and the conversion rounds to
    # nearest, so it can land just ABOVE the ceiling. Never round up past it: if
    # the ceiling is a motion-blur limit, exceeding it defeats the point, and if
    # it is the frame-length limit, exceeding it corrupts the frame. One step is
    # always enough, since the rounding error is under a line.
    if (coarse > description["exposure"]["coarse"]["min"]
            and sensors.exposure_ns_of(description, coarse, mode_name) > ceiling):
        coarse -= 1

    gain_code = sensors.gain_code(description, gain_q8)

    return {
        "measured_q8": measured,
        "target_q8": target_q8,
        # Report what the sensor will ACTUALLY do, not what was requested: the
        # difference is up to a line time and a gain step, and feeding the
        # request rather than the outcome back into the loop is what makes an
        # AE loop drift.
        "exposure_ns": sensors.exposure_ns_of(description, coarse, mode_name),
        "gain_q8": sensors.gain_of_code(description, gain_code),
        "coarse_integration": coarse,
        "analogue_gain_code": gain_code,
        "converged": abs(measured - target_q8) * 16 < target_q8,
    }
