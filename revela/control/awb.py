# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Auto white balance: choose per-CFA-colour gains from the colour statistics.

Reads the statistics block's per-zone R, G and B sums and produces the Q8.8
gains the white balance block applies. Pure software, once per frame.

Grey world, and its failure mode
--------------------------------

The default estimator assumes the average of a scene is neutral, so whatever
imbalance the per-channel sums show is the illuminant's, and dividing it out
balances the image. It is cheap, it needs no calibration, and it is right often
enough to be the standard starting point.

It is also wrong in a specific and predictable way: a scene genuinely dominated
by one colour -- a field of grass, a red brick wall, a close-up of a face --
violates the assumption directly, and grey world will dutifully remove the
colour that was actually there. Every mitigation below exists because of that.

  ZONE FILTERING. Zones that are saturated or crushed are excluded, because a
  clipped channel understates its own contribution and biases the estimate
  toward whichever channel clipped first.

  GREY-POINT SELECTION. Only zones whose colour is already near neutral vote.
  A zone that is strongly coloured is evidence about the subject, not about the
  illuminant. This is what stops a lawn turning grey.

  GAIN LIMITS. The result is clamped to a plausible illuminant range. No real
  illuminant needs a 4x red gain, so a computation demanding one has been misled
  and should be ignored rather than obeyed.

Green is the reference and stays at unity. Scaling R and B relative to G rather
than normalising all three keeps the signal from clipping earlier than it needs
to, and G is the channel with the most samples and the best signal-to-noise.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import stats as stats_block
from revela.control import Q8, clamp, damp
from revela.blocks.whitebalance import registers_from_gains

# Plausible illuminant range for the R and B gains, Q8.8. Roughly 0.5x to 4x,
# which comfortably spans tungsten through deep shade.
MIN_GAIN_Q8 = Q8 // 2
MAX_GAIN_Q8 = Q8 * 4

# A zone votes only if each channel's mean is inside these bounds, as a Q8.8
# fraction of full scale.
SATURATION_MARGIN_Q8 = 250
CRUSH_MARGIN_Q8 = 8

# How far from neutral a zone may be and still count as evidence about the
# illuminant, as a Q8.8 ratio against green.
GREY_TOLERANCE_Q8 = Q8 * 3 // 2      # within about 1.5x of neutral either way

DAMPING = (1, 4)


def grey_zones(statistics: np.ndarray, bit_depth: int) -> np.ndarray:
    """Boolean mask of zones usable as evidence about the illuminant."""
    records = np.asarray(statistics, dtype=np.int64)
    # Per-SAMPLE means: green has twice the samples of red and blue, so the raw
    # sums are not comparable and a ratio taken from them is wrong by 2x.
    means = stats_block.colour_means(records)
    counts = records[:, stats_block.STATS_LAYOUT.index("count")]

    red, green, blue = means[:, 0], means[:, 1], means[:, 2]
    full_scale = (1 << bit_depth) - 1
    normalised = means * Q8 // full_scale

    exposed = (
        (counts > 0)
        & (normalised.max(axis=1) < SATURATION_MARGIN_Q8)
        & (normalised.min(axis=1) > CRUSH_MARGIN_Q8)
    )

    safe_green = np.maximum(green, 1)
    red_ratio = red * Q8 // safe_green
    blue_ratio = blue * Q8 // safe_green
    neutral = (
        (red_ratio < GREY_TOLERANCE_Q8) & (red_ratio > Q8 * Q8 // GREY_TOLERANCE_Q8)
        & (blue_ratio < GREY_TOLERANCE_Q8) & (blue_ratio > Q8 * Q8 // GREY_TOLERANCE_Q8)
    )
    return exposed & neutral


def solve(statistics: np.ndarray, *, bit_depth: int,
          current_gains_q8: tuple[int, int] = (Q8, Q8),
          grey_world_fallback: bool = True) -> dict:
    """One AWB iteration: statistics in, white balance gains out.

    Args:
        statistics: ``(zones, 5)`` records for the frame just completed.
        bit_depth: datapath width.
        current_gains_q8: the ``(red, blue)`` gains the measured frame used, so
            the result can be damped against them.
        grey_world_fallback: if no zone qualifies as neutral, fall back to plain
            grey world over the correctly-exposed zones. Without this a scene
            with no neutral content freezes the loop, which looks like a bug even
            though holding is arguably the more defensible response.

    Returns:
        ``gain_red_q8``, ``gain_blue_q8``, ``gain_green_q8`` (always unity), the
        four register values keyed by CFA colour for the white balance block, and
        ``zones_used`` so a caller can tell a confident estimate from a guess.
    """
    records = np.asarray(statistics, dtype=np.int64)
    mask = grey_zones(records, bit_depth)

    # The fallback estimate is not equally trustworthy: it includes zones that
    # were rejected as saturated, crushed or strongly coloured, which are exactly
    # the zones that mislead grey world. Report it as such, so a caller can hold
    # the previous gains or widen its search rather than acting on it.
    fell_back = False
    if not mask.any() and grey_world_fallback:
        counts = records[:, stats_block.STATS_LAYOUT.index("count")]
        mask = counts > 0
        fell_back = True

    if not mask.any():
        red_gain, blue_gain = current_gains_q8
        return _result(red_gain, blue_gain, zones_used=0, confident=False)

    # Area-weighted totals, each scaled by how many samples of that colour a zone
    # actually contains, so the three are directly comparable.
    divisor = stats_block.CFA_SAMPLE_DIVISOR
    sum_r = int(records[mask, 0].sum()) * divisor[0]
    sum_g = int(records[mask, 1].sum()) * divisor[1]
    sum_b = int(records[mask, 2].sum()) * divisor[2]

    if sum_r <= 0 or sum_b <= 0 or sum_g <= 0:
        red_gain, blue_gain = current_gains_q8
        return _result(red_gain, blue_gain, zones_used=int(mask.sum()), confident=False)

    # Green is the reference; R and B are scaled to match it. Green's own gain
    # stays at unity so nothing is scaled up without reason.
    target_red = clamp(sum_g * Q8 // sum_r, MIN_GAIN_Q8, MAX_GAIN_Q8)
    target_blue = clamp(sum_g * Q8 // sum_b, MIN_GAIN_Q8, MAX_GAIN_Q8)

    red_gain = damp(current_gains_q8[0], target_red, *DAMPING)
    blue_gain = damp(current_gains_q8[1], target_blue, *DAMPING)

    return _result(red_gain, blue_gain, zones_used=int(mask.sum()),
                   confident=not fell_back)


def _result(red_gain: int, blue_gain: int, zones_used: int, confident: bool) -> dict:
    red_gain = clamp(red_gain, MIN_GAIN_Q8, MAX_GAIN_Q8)
    blue_gain = clamp(blue_gain, MIN_GAIN_Q8, MAX_GAIN_Q8)
    return {
        "gain_red_q8": red_gain,
        "gain_green_q8": Q8,
        "gain_blue_q8": blue_gain,
        # The colour-to-register convention -- greens at unity, both greens
        # equal -- is the block's to state, in registers_from_gains. Spelling
        # the register names here would be a second copy of it.
        "registers": registers_from_gains(r=red_gain, b=blue_gain, g=Q8),
        "zones_used": zones_used,
        "confident": confident,
    }
