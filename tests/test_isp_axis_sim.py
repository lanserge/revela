# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The stream-to-AXIS adapter, simulated: beats leave once and in order,
at a beat a cycle, a reset drops none it has taken, and its ready is a
register."""
import shutil
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parents[1] / "revela" / "boards" / "common"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="needs iverilog")


def test_the_adapter_passes_every_beat_once_behind_a_registered_ready(
        tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_isp_axis_flow.v"),
                    str(COMMON / "hdl" / "isp_axis.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=300)
    assert "isp_axis: all checks passed" in run.stdout, run.stdout[-3000:]


def test_an_offered_beat_survives_the_adapters_reset(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-DSIMULATION", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_isp_axis.v"),
                    str(COMMON / "hdl" / "isp_axis.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=300)
    assert "isp axis: all checks passed" in run.stdout, run.stdout[-3000:]
