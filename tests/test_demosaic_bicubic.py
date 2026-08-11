# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Bicubic demosaic: the NumPy model, and its bit-exact Verilog.

Rule 3, same shape as the bilinear suite: a register-free block whose whole
behaviour is the site table, checked against an independently written
per-pixel window walk. What is NEW versus bilinear -- the negative lobes --
gets its own pins: overshoot must clip, not wrap, and division must floor
on negative sums exactly as NumPy's // does.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_verilator, run_cocotb

from revela.blocks.demosaic import bicubic as bicubic_module
from revela.stream import StreamSpec

bicubic = bicubic_module.bicubic

WIDTH = 16
HEIGHT = 12
BIT_DEPTH = 12
TOP = (1 << BIT_DEPTH) - 1
CUBIC = (-1, 9, 9, -1)
OFFSETS = (-3, -1, 1, 3)


def oracle(frame: np.ndarray, phase: int) -> np.ndarray:
    """(h, w, 3) reference: an explicit 7x7 window walk, none of the model's
    slicing, the same Keys half-phase kernel and floor/clip rules."""
    phase_row, phase_col = (phase >> 1) & 1, phase & 1
    height, width = frame.shape
    padded = np.pad(frame.astype(np.int64), 3, mode="edge")
    out = np.zeros((height, width, 3), dtype=np.int64)
    for y in range(height):
        for x in range(width):
            win = padded[y:y + 7, x:x + 7]      # centre at (3, 3)
            centre = int(win[3, 3])
            h4 = sum(w * win[3, 3 + o] for w, o in zip(CUBIC, OFFSETS))
            v4 = sum(w * win[3 + o, 3] for w, o in zip(CUBIC, OFFSETS))
            d16 = sum(wr * wc * win[3 + r, 3 + c]
                      for wr, r in zip(CUBIC, OFFSETS)
                      for wc, c in zip(CUBIC, OFFSETS))
            horiz = min(max(h4 // 16, 0), TOP)
            vert = min(max(v4 // 16, 0), TOP)
            cross = min(max((h4 + v4) // 32, 0), TOP)
            diag = min(max(d16 // 256, 0), TOP)
            r_row = (y & 1) == phase_row
            r_col = (x & 1) == phase_col
            if r_row and r_col:
                out[y, x] = (centre, cross, diag)
            elif r_row:
                out[y, x] = (horiz, centre, vert)
            elif r_col:
                out[y, x] = (vert, centre, horiz)
            else:
                out[y, x] = (diag, cross, centre)
    return out


def raw_frame(rng, width=WIDTH, height=HEIGHT) -> np.ndarray:
    frame = rng.integers(0, TOP + 1, (height, width)).astype(np.uint16)
    # A hard step through the kernel's reach: overshoot in both directions.
    frame[:, :3] = 0
    frame[:, 3:6] = TOP
    return frame


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("phase", range(4))
def test_matches_the_per_pixel_oracle(rng, phase):
    """THE model test: every pixel, every phase, against the window walk."""
    frame = raw_frame(rng)
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    np.testing.assert_array_equal(out, oracle(frame, phase))


@pytest.mark.parametrize("phase", range(4))
def test_flat_field_stays_flat(rng, phase):
    """Every kernel sums to its divisor, so a constant frame is exact."""
    for level in (0, 1, 1000, TOP):
        frame = np.full((HEIGHT, WIDTH), level, dtype=np.uint16)
        out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
        np.testing.assert_array_equal(out, np.full((HEIGHT, WIDTH, 3), level))


def test_measured_samples_pass_through(rng):
    """At each site, the measured channel is the input, untouched."""
    frame = raw_frame(rng)
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    np.testing.assert_array_equal(out[0::2, 0::2, 0], frame[0::2, 0::2])  # R
    np.testing.assert_array_equal(out[0::2, 1::2, 1], frame[0::2, 1::2])  # Gr
    np.testing.assert_array_equal(out[1::2, 0::2, 1], frame[1::2, 0::2])  # Gb
    np.testing.assert_array_equal(out[1::2, 1::2, 2], frame[1::2, 1::2])  # B


def test_overshoot_clips_high_and_low():
    """The cubic lobes overshoot a step edge by up to top/8 -- the result
    must SATURATE, not wrap. Both directions, pinned by construction.

    Sample construction (phase 0, row 0 is an R row): at a Gr site whose
    row reads [0, top, top, 0] on the R lattice, h4 = 9*top + 9*top - 0
    ... scaled cases below are chosen so the raw sum is negative once and
    above full scale once.
    """
    frame = np.zeros((6, 8), dtype=np.uint16)
    # R lattice on row 0 (cols 0,2,4,6). Site (0,3) is Gr: its R estimate
    # reads cols 0,2,4,6 with weights -1,9,9,-1.
    frame[0, [0, 2, 4, 6]] = (TOP, 0, 0, TOP)      # h4 = -2*TOP -> clips to 0
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[0, 3, 0] == 0
    frame[0, [0, 2, 4, 6]] = (0, TOP, TOP, 0)      # h4 = 18*TOP -> clips high
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[0, 3, 0] == TOP


def test_division_floors_like_numpy():
    """A slightly negative sum floors toward -infinity and then clips: the
    signed shift in hardware and NumPy's // must be the same operation."""
    frame = np.zeros((6, 8), dtype=np.uint16)
    frame[0, [0, 2, 4, 6]] = (1, 0, 0, 0)          # h4 = -1 -> // 16 = -1 -> 0
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[0, 3, 0] == 0
    frame[0, [0, 2, 4, 6]] = (0, 1, 1, 0)          # h4 = 18 -> // 16 = 1
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert out[0, 3, 0] == 1


def test_borders_replicate():
    """The kernel reaches 3 pixels past the edge; all of them replicate.

    A flat bright frame must stay flat AT the border too -- any zero
    leaking into the pad would dent the first columns by up to 1/16.
    """
    frame = np.full((6, 8), 3000, dtype=np.uint16)
    out = bicubic.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    np.testing.assert_array_equal(out, np.full((6, 8, 3), 3000))


def test_a_multi_channel_stream_fails_at_the_trace():
    """No meaning tags: the arithmetic is the contract. This block eats one
    sample per pixel; a packed multi-channel word has no phase slices and
    no plain samples to offset, so the trace itself dies at compose time,
    before any Verilog exists."""
    with pytest.raises(Exception):
        bicubic.generate(StreamSpec(bit_depth=BIT_DEPTH, channels=3),
                         8, 8, module_name="revela_demosaic_bicubic")


def test_odd_dimensions_are_refused(rng):
    with pytest.raises(ValueError, match="odd dimension"):
        bicubic.run(np.zeros((5, 8), dtype=np.uint16), {},
                    bit_depth=BIT_DEPTH, bayer_phase=0)


# --------------------------------------------------------------------------- #
# Rule 3: bit-exact model versus generated Verilog
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_verilog_is_bit_exact_with_the_model(tmp_path, rng):
    """THE test for this block.

    Four trials in one elaboration, one per CFA phase, each on a frame
    with a hard step through the kernel's reach (overshoot both ways),
    under randomised backpressure. Six line buffers, signed accumulators,
    floor shifts and saturation -- all traced, none hand-written.
    """
    spec = StreamSpec(bit_depth=BIT_DEPTH)
    generated = bicubic.generate(spec, WIDTH, HEIGHT,
                                 module_name="revela_demosaic_bicubic")

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
            "block": "bicubic",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": BIT_DEPTH,
            "channels": 3,
            "trials": trials,
        },
    )
