# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The time-of-flight ISP, its frame store included, is its models.

`tof_isp` holds np2hw's frame store and the ToF core of model blocks, so it
is proved as a design (np2hw.pipeline.proof): the store's model and the
blocks' models against the design's own Verilog, one random stimulus
driving both -- the sensor's phase frames in bursts of four, as the module
sends them, its eleven-bit samples widened as the link widens them, and a
consumer that takes the depth frames at its own pace, waiting and stalling.
Every depth frame out, and which frame it was computed at, is the models'.
The store keeps its frames in block RAM here; its external-memory binding
is held to the same model by np2hw's own tests.
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from np2hw.design import composite
from np2hw.hdl.models import Stimulus, frame_store
from np2hw.pipeline import proof

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None,
                                reason="verilator not on PATH")

W, H, SLOTS = 16, 8, 8
# the store's latency, and the core's stages behind it
MARGIN = 4 + 80


def _design():
    return composite.load("revela.pipelines.tof_isp:tof_isp", {
        "implementation": "'bram'", "bit_depth": 16, "max_width": W,
        "max_height": H, "headroom": 1, "slots": SLOTS})


def _draw(rng, frames, ports, outputs=8):
    """Bursts of four, each frame of a burst as close as the store tells
    apart, and a consumer whose every wait is drawn until the choice it
    leads to is clear."""
    params = {"FRAMES": 4, "SLOTS": SLOTS}
    gaps = tuple(int(rng.integers(2 * MARGIN, 2 * MARGIN + 40)) if k % 4
                 else int(rng.integers(3 * MARGIN, 3 * MARGIN + 400))
                 for k in range(len(frames)))
    stalls = tuple(tuple(sorted({(int(rng.integers(0, W * H)),
                                  int(rng.integers(1, 20)))
                                 for _ in range(int(rng.integers(0, 3)))}))
                   for _ in range(outputs))

    def clear(first, waits):
        stim = Stimulus(gaps, first, tuple(waits), stalls)
        n = len(waits) + 2
        t = frame_store(frames, ports, stim, params, outputs=n).timeline
        return not t.close(MARGIN, upto=n) and not t.overrun

    t0 = frame_store(frames, ports, Stimulus(gaps, 0, (), stalls), params,
                     outputs=1).timeline
    for _ in range(1000):
        first = t0.chosen[0] + MARGIN + 1 + int(rng.integers(0, 4000))
        if clear(first, []):
            break
    waits = []
    for _ in range(outputs - 1):
        for _ in range(1000):
            wait = int(rng.integers(MARGIN + 2, 6000))
            if clear(first, waits + [wait]):
                waits.append(wait)
                break
        else:
            raise AssertionError("no clear wait")
    return Stimulus(gaps, first, tuple(waits), stalls)


@pytest.mark.parametrize("seed", range(2))
def test_the_tof_isp_is_its_models_frame_for_frame(seed):
    design = _design()
    rng = np.random.default_rng(seed)
    count = 20
    # four phase planes of eleven-bit correlations, the sensor's, in turn;
    # each word the twelve-bit link word widened to sixteen
    planes = rng.integers(-1024, 1024, (4, H, W))
    frames = [(planes[k % 4] & 0x7FF) << 4 for k in range(count)]
    ports = [{"in_frame_width": W, "in_frame_height": H, "ctx_frame_seq": k}
             for k in range(count)]
    stim = _draw(rng, frames, [{"in_ctx": p["ctx_frame_seq"]} for p in ports])
    model = proof.run_model(design, frames, ports, stim)
    rtl = proof.run_rtl(design, frames, ports, stim)
    assert len(rtl.frames) == len(model.frames) == len(stim.waits) + 1
    for i, (got, want) in enumerate(zip(rtl.frames, model.frames)):
        assert got.tolist() == proof.words(want, rtl.word_bits).tolist(), \
            f"depth frame {i}"
    for i, (got, want) in enumerate(zip(rtl.ports, model.ports)):
        assert got["out_source"] == want["out_source"], f"depth frame {i}"
    # and the depth is a depth: something measured, somewhere
    assert any((np.asarray(f)[..., 0] > 0).any() for f in model.frames)
