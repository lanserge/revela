# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Bilinear demosaic: the NumPy model, and its bit-exact Verilog.

Rule 3, same shape as the other block suites. The block is register-free --
everything it does is decided by the pixel's CFA position -- so the model
tests concentrate on the site table (which tap combination feeds which
channel, at every phase), the truncating shifts, and the replicated border,
each against an independently written per-pixel oracle rather than a
rearrangement of the model's own slices.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks.demosaic import bilinear as bilinear_module
from revela.stream import StreamSpec

bilinear = bilinear_module.bilinear

WIDTH = 16
HEIGHT = 8
BIT_DEPTH = 12
TOP = (1 << BIT_DEPTH) - 1


def oracle(frame: np.ndarray, phase: int) -> np.ndarray:
    """(h, w, 3) reference, written per pixel with none of the model's slicing.

    Same definition of bilinear -- centre / cross / diag / horiz / vert with
    truncating integer division and replicated borders -- but arrived at
    through an explicit window walk, so a slicing or phase-mapping mistake in
    the model cannot also be in the oracle.
    """
    phase_row, phase_col = (phase >> 1) & 1, phase & 1
    height, width = frame.shape
    padded = np.pad(frame.astype(np.int64), 1, mode="edge")
    out = np.zeros((height, width, 3), dtype=np.int64)
    for y in range(height):
        for x in range(width):
            win = padded[y:y + 3, x:x + 3]
            centre = int(win[1, 1])
            cross = (win[0, 1] + win[2, 1] + win[1, 0] + win[1, 2]) // 4
            diag = (win[0, 0] + win[0, 2] + win[2, 0] + win[2, 2]) // 4
            horiz = (win[1, 0] + win[1, 2]) // 2
            vert = (win[0, 1] + win[2, 1]) // 2
            r_row = (y & 1) == phase_row
            r_col = (x & 1) == phase_col
            if r_row and r_col:
                out[y, x] = (centre, cross, diag)       # R site
            elif r_row:
                out[y, x] = (horiz, centre, vert)       # Gr site
            elif r_col:
                out[y, x] = (vert, centre, horiz)       # Gb site
            else:
                out[y, x] = (diag, cross, centre)       # B site
    return out


def raw_frame(rng, width=WIDTH, height=HEIGHT) -> np.ndarray:
    frame = rng.integers(0, TOP + 1, (height, width)).astype(np.uint16)
    # Rails in the corner window: saturation and truncation defects live
    # where sums are largest and smallest.
    frame[0, 0], frame[0, 1], frame[1, 0], frame[1, 1] = 0, TOP, TOP, 0
    return frame


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("phase", range(4))
def test_matches_the_per_pixel_oracle(rng, phase):
    """THE model test: every pixel, every phase, against the window walk."""
    frame = raw_frame(rng)
    out = bilinear.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    np.testing.assert_array_equal(out, oracle(frame, phase))


@pytest.mark.parametrize("phase", range(4))
def test_flat_field_stays_flat(rng, phase):
    """A constant frame demosaics to that constant in all three channels.

    Averages of equal values are exact under the truncating shifts, so any
    deviation here is a phase-mapping error, not arithmetic.
    """
    for level in (0, 1, 1000, TOP):
        frame = np.full((HEIGHT, WIDTH), level, dtype=np.uint16)
        out = bilinear.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
        np.testing.assert_array_equal(out, np.full((HEIGHT, WIDTH, 3), level))


def test_measured_samples_pass_through(rng):
    """At each site, the channel that WAS measured is the input, untouched.

    Interpolation may be argued about; the measured sample may not. Phase 0
    (R at even/even) makes the site map explicit.
    """
    frame = raw_frame(rng)
    out = bilinear.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    np.testing.assert_array_equal(out[0::2, 0::2, 0], frame[0::2, 0::2])  # R
    np.testing.assert_array_equal(out[0::2, 1::2, 1], frame[0::2, 1::2])  # Gr
    np.testing.assert_array_equal(out[1::2, 0::2, 1], frame[1::2, 0::2])  # Gb
    np.testing.assert_array_equal(out[1::2, 1::2, 2], frame[1::2, 1::2])  # B


def test_division_truncates():
    """(1+1+1+2)/4 -> 1 and (1+2)/2 -> 1: shifts, not rounding.

    The declared design: divisors are powers of two and there is no
    rounding constant anywhere. Pinned so nobody 'fixes' it into a bias
    change without meeting this test.
    """
    frame = np.zeros((4, 4), dtype=np.uint16)
    # Phase 0: (1,1) is a B site; its G is cross of (0,1),(2,1),(1,0),(1,2).
    frame[0, 1], frame[2, 1], frame[1, 0], frame[1, 2] = 1, 1, 1, 2
    out = bilinear.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[1, 1, 1] == 1
    # Its R is the diagonal average of (0,0),(0,2),(2,0),(2,2) = 0.
    assert out[1, 1, 0] == 0


def test_borders_replicate():
    """The frame edge is demosaiced against copies of itself, not zeros.

    A single hot column at x=0: the R value interpolated AT the edge must
    see the replicated column, not a dark border that would fringe it.
    """
    frame = np.zeros((4, 4), dtype=np.uint16)
    frame[:, 0] = 1000
    out = bilinear.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    # (1,0) is a Gb site (phase 0): its R is vert = mean of (0,0),(2,0) = 1000;
    # its B is horiz = mean of x[-1] (replicated 1000) and (1,1)=0 -> 500.
    assert out[1, 0, 0] == 1000
    assert out[1, 0, 2] == 500


def test_a_multi_channel_stream_fails_at_the_trace():
    """No meaning tags: the arithmetic is the contract. This block eats one
    sample per pixel; a packed multi-channel word has no phase slices and
    no plain samples to offset, so the trace itself dies at compose time,
    before any Verilog exists."""
    with pytest.raises(Exception):
        bilinear.generate(StreamSpec(bit_depth=BIT_DEPTH, channels=3),
                          8, 8, module_name="revela_demosaic_bilinear")


def test_odd_dimensions_are_refused(rng):
    """A CFA-indexed block has no consistent phase on an odd frame."""
    with pytest.raises(ValueError, match="odd dimension"):
        bilinear.run(np.zeros((5, 8), dtype=np.uint16), {},
                     bit_depth=BIT_DEPTH, bayer_phase=0)


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Four trials in one elaboration, one per CFA phase, each on a fresh frame
    with rails in the corner window, under randomised backpressure on both
    sides. One build serving all four phases is the point: ``bayer_phase``
    is a runtime register, and the tap-combination mux must follow it.
    """
    spec = StreamSpec(bit_depth=BIT_DEPTH)
    generated = bilinear.generate(spec, WIDTH, HEIGHT,
                                  module_name="revela_demosaic_bilinear")

    trials = []
    for phase in range(4):
        frame = raw_frame(rng)
        trials.append({
            "seed": int(rng.integers(0, 2**31)),
            "context": {"bayer_phase": phase},
            "values": {},
            "frame": frame.ravel().tolist(),
        })

    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "bilinear",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "channels": 3,
            "trials": trials,
        },
    )
