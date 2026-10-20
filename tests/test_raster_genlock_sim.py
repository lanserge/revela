# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The raster following a stream, simulated: a frame start arriving off
its target is answered by fine-phase steps, the error in pixels times the
steps a pixel is, the right way round; one inside the dead band by none;
one beyond steering by a single jam, then a cooled raster that jams no
more."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "revela" / "boards" / "common"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="needs iverilog")

# the bench's raster: 100-pixel lines, the window at line 10, the target
# two lines ahead of it, seven steps a pixel
H_TOTAL, TARGET, PS = 100, 8, 7


def test_the_raster_steers_by_the_arrival_error_and_jams_once(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_raster_genlock.v"),
                    str(COMMON / "hdl" / "raster.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=600)
    assert "genlock: done" in run.stdout, run.stdout[-3000:]
    got = {int(n): (int(s), int(r), int(j)) for n, s, r, j in re.findall(
        r"ARRIVAL (\d+) at \S+: steps=(\d+) retard=(\d+) jams=(\d+)",
        run.stdout)}

    def late(x, y):                     # pixels past the target
        return (y - TARGET) * H_TOTAL + x

    assert got[1] == (late(40, 8) * PS, late(40, 8) * PS, 0)
    assert got[2] == (-late(30, 7) * PS, 0, 0)
    assert got[3] == (late(5, 9) * PS, late(5, 9) * PS, 0)
    assert got[4] == (0, 0, 0)          # one pixel: the dead band
    assert got[5] == (0, 0, 1)          # five lines: jammed, once
    assert got[6] == (0, 0, 0)          # cooled: neither jam nor steer


def _smooth(tmp_path, steer=0, drift=20, start=0, frames=160, quiet=0):
    """The closed-loop bench: each frame's arrival error (pixels), the
    steps the raster made over it (positive retards), the jams so far,
    the closest and widest gaps between its steps, and how many of the
    stream's pixels reached the sink."""
    vvp = tmp_path / f"smooth_{steer}_{drift}_{start}_{quiet}.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    f"-Ptb_raster_smooth.STEER={steer}",
                    f"-Ptb_raster_smooth.DRIFT={drift}",
                    f"-Ptb_raster_smooth.START={start}",
                    f"-Ptb_raster_smooth.FRAMES={frames}",
                    f"-Ptb_raster_smooth.QUIET={quiet}",
                    str(COMMON / "sim" / "tb_raster_smooth.v"),
                    str(COMMON / "hdl" / "raster.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=600)
    assert "smooth: done" in run.stdout, run.stdout[-3000:]
    return [tuple(int(v) for v in row) for row in re.findall(
        r"FRAME \d+ err=(-?\d+) steps=(-?\d+) jams=(\d+) gap=(\d+)\.\.(\d+) "
        r"shown=(\d+)",
        run.stdout)]


def test_the_smooth_raster_steers_a_constant_trim_spread_evenly(tmp_path):
    """A source gaining 20 steps a frame is followed by 20 steps a frame
    the other way -- the crystals' difference learned -- each frame's
    steps evenly spaced across it, the error held within a pixel, and
    never a jam: what a television sees is a clock a little off nominal,
    constant."""
    rows = _smooth(tmp_path, drift=20)
    assert all(j == 0 for _, _, j, *_ in rows)
    for err, steps, _, lo, hi, _ in rows[-30:]:
        assert abs(err) <= 2, rows[-30:]
        assert steps == -20, rows[-30:]
        assert hi - lo <= 1 and lo >= 16, rows[-30:]   # 5000 / 20 = 250


def test_the_smooth_raster_pulls_in_without_jamming(tmp_path):
    """A frame start 150 pixels late, a source losing 20 steps a frame:
    pulled in under the two lines a jam waits for, then held."""
    rows = _smooth(tmp_path, drift=-20, start=150)
    assert all(j == 0 for _, _, j, *_ in rows)
    assert max(abs(e) for e, *_ in rows) < 200
    assert all(abs(e) <= 2 and s == 20 for e, s, *_ in rows[-30:]), rows[-30:]


def test_steer_none_neither_steps_nor_jams(tmp_path):
    """steer 2: the raster runs free -- a source drifting away and a frame
    start five lines off are answered by no step and no jam."""
    rows = _smooth(tmp_path, steer=2, drift=200, start=500, frames=20)
    assert all(s == 0 and j == 0 for _, s, j, *_ in rows), rows


def test_a_new_raster_paints_the_fill_while_the_television_finds_it(tmp_path):
    """G_QUIET frames after the raster starts show none of the stream --
    a television finds a 1.485 Gb/s signal on a dark picture at once and
    on a dense one in minutes -- while the genlock runs beneath, so the
    first frame shown is already locked; after them, every frame shows,
    as every frame does with no quiet at all."""
    loud = [r[5] for r in _smooth(tmp_path, quiet=0, frames=12)]
    rows = _smooth(tmp_path, quiet=6, frames=12)
    shown = [r[5] for r in rows]
    assert all(n > 0 for n in loud[1:]), loud
    # the bench's first frame start comes two raster frames in, so six
    # dark frames are the first four it reports on
    assert shown[1:5] == [0] * 4 and all(n > 0 for n in shown[5:]), shown
    assert all(j == 0 for _, _, j, *_ in rows)
