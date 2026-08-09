# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The 3A loops, driven against a synthetic sensor.

These are software loops, so the test is convergence and stability rather than
bit-exactness: given a scene, does the loop reach the right answer, and does it
get there without oscillating? A control loop that arrives at the right value by
overshooting on every frame is not working, and only a multi-frame test shows it.
"""
from __future__ import annotations

import numpy as np
import pytest

from revela import sensors
from revela.blocks import stats as stats_block
from revela.control import Q8, ae, af, awb

ZONES = 4
BIT_DEPTH = 10
FRAME = (32, 32)
WINDOW = (0, 0, FRAME[1], FRAME[0])
FULL_SCALE = (1 << BIT_DEPTH) - 1


@pytest.fixture
def imx219() -> dict:
    return sensors.load("imx219")


@pytest.fixture
def registers():
    return stats_block.model.params.bind(
        stats_block.default_registers(ZONES, ZONES, WINDOW))


def synthetic_frame(level: float, cast=(1.0, 1.0, 1.0)) -> np.ndarray:
    """A flat scene at ``level`` of full scale, with a per-channel cast.

    Written as a CFA mosaic, so the statistics block sees exactly what a sensor
    would deliver, including the two green samples per tile that make the raw
    colour sums non-comparable.
    """
    frame = np.zeros(FRAME, dtype=np.uint16)
    for dy in (0, 1):
        for dx in (0, 1):
            channel = {(0, 0): cast[0], (0, 1): cast[1],
                       (1, 0): cast[1], (1, 1): cast[2]}[(dy, dx)]
            frame[dy::2, dx::2] = min(FULL_SCALE, int(level * FULL_SCALE * channel))
    return frame


def measure(frame: np.ndarray, registers) -> np.ndarray:
    return stats_block.model(frame, registers, bayer_phase=0, window=WINDOW)


# --------------------------------------------------------------------------- #
# The statistics the loops read
# --------------------------------------------------------------------------- #

def test_green_is_normalised_by_its_sample_count(registers):
    """Two green samples per tile: the raw sums are not comparable.

    A neutral scene has roughly twice the green SUM of the red sum simply
    because there are twice as many green pixels. An estimator comparing the raw
    sums would read that as a green cast and converge confidently on the wrong
    white balance, so the per-sample normalisation is tested directly.
    """
    statistics = measure(synthetic_frame(0.5), registers)

    raw = np.asarray(statistics, dtype=np.int64)
    assert raw[0, 1] == pytest.approx(raw[0, 0] * 2, rel=0.01), (
        "the raw green sum should be about twice the red sum on a neutral scene")

    means = stats_block.colour_means(statistics)
    assert means[0, 0] == means[0, 1] == means[0, 2], (
        "per-sample means must be equal on a neutral scene")


def test_luma_weights_are_prescaled_for_the_cfa(registers):
    """sum_y must track scene brightness, not green's sample advantage."""
    statistics = measure(synthetic_frame(0.5), registers)
    luma = stats_block.zone_means(statistics)[:, stats_block.STATS_LAYOUT.index("sum_y")]
    # A flat 50% neutral scene should meter at about 50% of full scale.
    assert luma.mean() == pytest.approx(FULL_SCALE * 0.5, rel=0.05)


# --------------------------------------------------------------------------- #
# Auto exposure
# --------------------------------------------------------------------------- #

def test_exposure_converges_to_the_target(imx219, registers):
    """The loop must reach the metering target and then stay there."""
    exposure_ns, gain_q8 = 2_000_000, Q8
    history = []

    for _ in range(25):
        # Synthetic sensor: signal is proportional to exposure x gain.
        level = exposure_ns * gain_q8 / (Q8 * 20_000_000)
        result = ae.solve(
            imx219, measure(synthetic_frame(level), registers),
            current_exposure_ns=exposure_ns, current_gain_q8=gain_q8,
            bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES)
        exposure_ns, gain_q8 = result["exposure_ns"], result["gain_q8"]
        history.append(result["measured_q8"])

    assert result["converged"], (
        f"AE did not converge: measured {history[-1]}, "
        f"target {ae.DEFAULT_TARGET_Q8}, history {history}")
    assert abs(history[-1] - ae.DEFAULT_TARGET_Q8) <= 4


def test_exposure_does_not_oscillate(imx219, registers):
    """Damped, not hunting: the last few frames must be settled.

    An undamped loop against the sensor's command latency oscillates, which is
    far more objectionable to a viewer than being slightly off and steady.
    """
    exposure_ns, gain_q8 = 20_000_000, Q8
    measured = []
    for _ in range(30):
        level = exposure_ns * gain_q8 / (Q8 * 20_000_000)
        result = ae.solve(
            imx219, measure(synthetic_frame(level), registers),
            current_exposure_ns=exposure_ns, current_gain_q8=gain_q8,
            bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES)
        exposure_ns, gain_q8 = result["exposure_ns"], result["gain_q8"]
        measured.append(result["measured_q8"])

    tail = measured[-6:]
    assert max(tail) - min(tail) <= 3, f"AE is still hunting: {tail}"


def test_exposure_is_preferred_over_gain(imx219, registers):
    """Gain amplifies read noise; exposure does not. Exposure goes first."""
    statistics = measure(synthetic_frame(0.02), registers)      # very dark
    result = ae.solve(
        imx219, statistics, current_exposure_ns=1_000_000, current_gain_q8=Q8,
        bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES)
    assert result["exposure_ns"] > 1_000_000
    assert result["gain_q8"] == Q8, (
        "gain was raised while exposure headroom remained")


def test_gain_is_used_once_exposure_hits_its_ceiling(imx219, registers):
    """With exposure capped, the loop must still respond to a dark scene."""
    statistics = measure(synthetic_frame(0.01), registers)
    ceiling = 2_000_000
    gain = Q8
    for _ in range(12):
        result = ae.solve(
            imx219, statistics, current_exposure_ns=ceiling, current_gain_q8=gain,
            bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES,
            max_exposure_ns=ceiling)
        gain = result["gain_q8"]
    assert result["exposure_ns"] <= ceiling
    assert gain > Q8, "gain never rose despite exposure being capped"


def test_exposure_reports_what_the_sensor_will_actually_do(imx219, registers):
    """Feed back the achievable value, not the requested one.

    Coarse integration is quantised to line times. A loop that feeds back its
    request rather than the outcome accumulates the quantisation error and
    drifts.
    """
    result = ae.solve(
        imx219, measure(synthetic_frame(0.3), registers),
        current_exposure_ns=5_000_000, current_gain_q8=Q8,
        bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES)
    expected = sensors.exposure_ns_of(imx219, result["coarse_integration"])
    assert result["exposure_ns"] == expected
    assert result["gain_q8"] == sensors.gain_of_code(
        imx219, result["analogue_gain_code"])


def test_saturated_scene_reduces_exposure(imx219, registers):
    statistics = measure(synthetic_frame(1.0), registers)
    result = ae.solve(
        imx219, statistics, current_exposure_ns=30_000_000, current_gain_q8=Q8 * 4,
        bit_depth=BIT_DEPTH, zones_x=ZONES, zones_y=ZONES)
    assert result["exposure_ns"] * result["gain_q8"] < 30_000_000 * Q8 * 4


def test_centre_weighting_favours_the_middle():
    weights = ae.centre_weights(4, 4).reshape(4, 4)
    assert weights[1, 1] == weights[1, 2] == weights[2, 1] == weights[2, 2]
    assert weights[1, 1] > weights[0, 0], "centre must outweigh the corners"


# --------------------------------------------------------------------------- #
# Auto white balance
# --------------------------------------------------------------------------- #

def test_white_balance_corrects_a_mild_cast(registers):
    """A mild cast is within the grey tolerance, so the estimate is confident."""
    statistics = measure(synthetic_frame(0.45, cast=(1.3, 1.0, 0.8)), registers)

    gains = (Q8, Q8)
    for _ in range(20):
        result = awb.solve(statistics, bit_depth=BIT_DEPTH, current_gains_q8=gains)
        gains = (result["gain_red_q8"], result["gain_blue_q8"])

    assert result["confident"], "a mild cast should not need the fallback"
    assert gains[0] == pytest.approx(Q8 / 1.3, rel=0.05)
    assert gains[1] == pytest.approx(Q8 / 0.8, rel=0.05)
    assert result["gain_green_q8"] == Q8, "green is the reference and stays unity"


def test_applying_the_gains_neutralises_the_scene(registers):
    """The actual point of the loop, stated as an outcome rather than a ratio."""
    statistics = measure(synthetic_frame(0.45, cast=(1.3, 1.0, 0.8)), registers)
    gains = (Q8, Q8)
    for _ in range(20):
        result = awb.solve(statistics, bit_depth=BIT_DEPTH, current_gains_q8=gains)
        gains = (result["gain_red_q8"], result["gain_blue_q8"])

    means = stats_block.colour_means(statistics)[0]
    balanced = [means[0] * gains[0] // Q8, means[1], means[2] * gains[1] // Q8]
    spread = max(balanced) - min(balanced)
    assert spread * 20 < max(balanced), (
        f"channels still differ after balancing: {balanced}")


def test_strongly_coloured_scene_falls_back_and_says_so(registers):
    """Grey world's failure mode, reported rather than hidden.

    A scene genuinely dominated by one colour has no neutral content, so there is
    no evidence about the illuminant. The loop still returns an estimate, but it
    must not claim confidence in it -- that flag is what lets a caller hold the
    previous gains instead of desaturating a lawn.
    """
    statistics = measure(synthetic_frame(0.45, cast=(2.0, 1.0, 0.4)), registers)
    result = awb.solve(statistics, bit_depth=BIT_DEPTH)
    assert not result["confident"]
    assert result["zones_used"] > 0


def test_saturated_scene_is_not_confident(registers):
    """A clipped channel understates itself and must not drive the estimate."""
    result = awb.solve(measure(synthetic_frame(1.0, cast=(1.6, 1.0, 0.6)), registers),
                       bit_depth=BIT_DEPTH)
    assert not result["confident"]


def test_gains_are_clamped_to_plausible_illuminants(registers):
    """No real illuminant needs a 10x gain; a computation demanding one is wrong."""
    statistics = measure(synthetic_frame(0.4, cast=(0.05, 1.0, 0.05)), registers)
    result = awb.solve(statistics, bit_depth=BIT_DEPTH)
    for key in ("gain_red_q8", "gain_blue_q8"):
        assert awb.MIN_GAIN_Q8 <= result[key] <= awb.MAX_GAIN_Q8


def test_registers_are_keyed_by_cfa_colour(registers):
    """The result must drop straight into the white balance block's registers."""
    result = awb.solve(measure(synthetic_frame(0.4), registers), bit_depth=BIT_DEPTH)
    assert set(result["registers"]) == {"gain_0_0", "gain_0_1", "gain_1_0", "gain_1_1"}
    # Both greens carry the same gain: a systematic Gr/Gb difference is a sensor
    # property, not something the illuminant did.
    assert result["registers"]["gain_0_1"] == result["registers"]["gain_1_0"] == Q8


def test_empty_statistics_hold_the_previous_gains():
    empty = np.zeros((ZONES * ZONES, len(stats_block.STATS_LAYOUT)), dtype=np.uint32)
    result = awb.solve(empty, bit_depth=BIT_DEPTH, current_gains_q8=(300, 400))
    assert (result["gain_red_q8"], result["gain_blue_q8"]) == (300, 400)
    assert not result["confident"]


# --------------------------------------------------------------------------- #
# Auto focus
# --------------------------------------------------------------------------- #

def test_the_loops_unity_is_the_registers_unity():
    """Two independent 256s, coupled at exactly one call site.

    The loop's arithmetic scale (control.Q8) and the whitebalance
    declaration's unity are separate facts that must be equal where awb
    hands gains to registers_from_gains. Nothing at runtime can check it --
    a variant block is a different declaration -- so the coupling is stated
    here, where it fails loudly if either side moves alone.
    """
    from revela.blocks.whitebalance import GAIN_ONE
    from revela.control import Q8

    assert Q8 == GAIN_ONE, (
        "the AWB loop's unity no longer matches the whitebalance "
        "declaration's; registers_from_gains(g=Q8) now mis-scales")


def test_autofocus_is_an_honest_stub():
    """It is blocked on a focus statistic that the hardware cannot yet produce."""
    with pytest.raises(NotImplementedError, match="focus figure"):
        af.solve()
