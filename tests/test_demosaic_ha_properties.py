# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Hamilton-Adams: metamorphic invariants and decision-boundary fuzz.

The oracle suite proves the model against a second reading by the SAME
author -- correlated by construction. These tests are decorrelated by
symmetry instead: each invariant is a theorem about the algorithm (exact,
not approximate), so it cannot share a transcription error with the model.
A swapped Gr/Gb role, a mirrored tap, a mis-routed output channel each
break one of them, loudly.

The fuzz half hammers the CLASSIFIER exactly where it can go wrong -- ties
and near-ties -- with a small-alphabet generator that makes |dh - dv| tiny
everywhere, checked against the per-pixel oracle.
"""
from __future__ import annotations

import numpy as np
import pytest

from revela.blocks.demosaic import hamilton_adams as ha

from test_demosaic_hamilton_adams import green_oracle

BIT_DEPTH = 12
TOP = (1 << BIT_DEPTH) - 1


def full(frame, phase):
    word = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)
    return ha.ha_rb.run(word, {}, bit_depth=BIT_DEPTH, bayer_phase=phase)


def frames(rng, n, lo=0, hi=TOP + 1, shape=(12, 16)):
    for _ in range(n):
        yield rng.integers(lo, hi, shape).astype(np.uint16)


# --------------------------------------------------------------------------- #
# Metamorphic invariants: theorems, checked exactly
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("phase", range(4))
def test_transpose_commutes(rng, phase):
    """demosaic(Fᵀ) == demosaic(F)ᵀ, with row/col phase bits swapped.

    Transposing swaps the roles of horizontal and vertical everywhere --
    gradients, Laplacians, the site table's Gr/Gb rows -- and the algorithm
    treats the axes symmetrically, so equality is EXACT. A swapped Gr/Gb
    role or an H/V asymmetry anywhere breaks this."""
    phase_t = (((phase & 1) << 1) | (phase >> 1)) & 3
    for frame in frames(rng, 3):
        a = np.transpose(full(frame, phase), (1, 0, 2))
        b = full(frame.T.copy(), phase_t)
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("phase", range(4))
def test_rotation_180_commutes(rng, phase):
    """Rotating the frame 180° flips both phase bits; results rotate."""
    for frame in frames(rng, 3):
        a = full(frame, phase)[::-1, ::-1]
        b = full(frame[::-1, ::-1].copy(), phase ^ 3)
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("phase", range(4))
def test_mirror_commutes(rng, phase):
    """A left-right mirror flips the column phase bit; results mirror."""
    for frame in frames(rng, 3):
        a = full(frame, phase)[:, ::-1]
        b = full(frame[:, ::-1].copy(), phase ^ 1)
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("phase", range(4))
def test_red_blue_are_symmetric(rng, phase):
    """Reinterpreting the CFA with R and B exchanged (both phase bits
    flipped, same frame) must exactly exchange the output channels: the
    algorithm has no red-favouring arithmetic anywhere."""
    for frame in frames(rng, 3):
        a = full(frame, phase)
        b = full(frame, phase ^ 3)
        np.testing.assert_array_equal(a, b[..., ::-1])


@pytest.mark.parametrize("phase", range(4))
def test_doubling_commutes_when_divisions_are_exact(rng, phase):
    """On frames of multiples of 32, every divider in BOTH stages is exact
    -- the deepest chain is the green TIE branch's //8 followed by the
    chroma //4, which is why 16 is not enough -- and the range is chosen
    so nothing clips even doubled. Under those conditions scaling the
    input by 2 scales the output by 2, exactly: any stray constant or
    asymmetric rounding anywhere breaks it."""
    # inputs <= 800; the chroma stage's corrected estimate stays below
    # TOP even at 2x, so clipping never engages on either side.
    for frame in frames(rng, 3, hi=26):
        frame = (frame * 32).astype(np.uint16)         # multiples of 32, <= 800
        np.testing.assert_array_equal(full((frame * 2).astype(np.uint16),
                                           phase),
                                      2 * full(frame, phase))


# --------------------------------------------------------------------------- #
# Decision-boundary fuzz: the classifier at and around its ties
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("phase", range(4))
def test_small_alphabet_fuzz_hits_the_ties(rng, phase):
    """Values from {0..3} make |dh - dv| tiny at nearly every site, so the
    tie and both near-tie branches are exercised thousands of times, each
    pixel checked against the independent window walk."""
    for frame in frames(rng, 30, hi=4):
        out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH,
                              bayer_phase=phase)
        np.testing.assert_array_equal(out[..., 1], green_oracle(frame, phase))


@pytest.mark.parametrize("phase", range(4))
def test_full_range_fuzz(rng, phase):
    """And the same at full scale, where clipping and signed floors bite."""
    for frame in frames(rng, 10):
        out = ha.ha_green.run(frame, {}, bit_depth=BIT_DEPTH,
                              bayer_phase=phase)
        np.testing.assert_array_equal(out[..., 1], green_oracle(frame, phase))


# --------------------------------------------------------------------------- #
# Committed micro-vectors: frozen truth, one per classifier branch
# --------------------------------------------------------------------------- #

def test_micro_vectors():
    """Hand-worked 5x5 windows, one per branch, frozen as literals.

    Worked at an R site (2,2), phase 0, so a future refactor answers to
    these numbers, not to any code. Horizontal branch, by hand:
        gw=100 ge=104 -> |gw-ge| = 4
        centre=200, X_ww=190, X_ee=194 -> lh = 2*200-190-194 = 16, dh = 20
        gn=10 gs=90 -> 80; lv = 2*200-0-0 = 400 -> dv = 480
        dh < dv: est = (100+104)//2 + 16//4 = 102 + 4 = 106.
    """
    f = np.zeros((6, 8), dtype=np.uint16)
    f[2, 1], f[2, 3] = 100, 104                    # gw, ge
    f[2, 0], f[2, 4] = 190, 194                    # X at +-2
    f[2, 2] = 200                                  # centre
    f[1, 2], f[3, 2] = 10, 90                      # gn, gs
    out = ha.ha_green.run(f, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert int(out[2, 2, 1]) == 106                # horizontal branch

    # Vertical branch: transpose the same construction by hand.
    #   gn=100 gs=104, lv = 2*200-190-194 = 16 -> dv = 20; dh = 480.
    #   est = (100+104)//2 + 16//4 = 106.
    f = np.zeros((6, 8), dtype=np.uint16)
    f[1, 2], f[3, 2] = 100, 104
    f[0, 2], f[4, 2] = 190, 194
    f[2, 2] = 200
    f[2, 1], f[2, 3] = 10, 90
    out = ha.ha_green.run(f, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert int(out[2, 2, 1]) == 106                # vertical branch

    # Tie: dh = dv = 0 + |lh| = |lv| = 2*50 = 100 each.
    #   est = (60+60+60+60)//4 + (100+100)//8 = 60 + 25 = 85.
    f = np.zeros((6, 8), dtype=np.uint16)
    f[2, 1] = f[2, 3] = f[1, 2] = f[3, 2] = 60
    f[2, 2] = 50                                   # lh = lv = 100
    out = ha.ha_green.run(f, {}, bit_depth=BIT_DEPTH, bayer_phase=0)
    assert int(out[2, 2, 1]) == 85                 # four-neighbour branch
