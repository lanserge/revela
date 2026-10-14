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
