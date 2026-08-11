# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The pipeline runner: designs as libraries of image functions.

The property that carries this feature is COMPOSABILITY: running to a port
and then from that port is the same computation as running straight
through. Everything else here is the refusals -- a wrong-shaped injection,
a register outside its range -- because a runner that guesses is a runner
that produces plausible wrong pictures.
"""
import shutil
import subprocess
import sys

import numpy as np
import pytest

from revela import cli

DESIGN = "pipelines/mono/imx219/color/pipeline.json"
PROFILE = "pipelines/mono/imx219/color/profiles/indoor.json"


@pytest.fixture()
def mosaic():
    rng = np.random.default_rng(7)
    return rng.integers(0, 1 << 10, size=(48, 64), dtype=np.int64)


def _run(*arguments) -> int:
    return cli.main(["run", *arguments])


def test_split_at_a_port_equals_one_run(tmp_path, mosaic):
    np.save(tmp_path / "in.npy", mosaic)

    assert _run(DESIGN, str(tmp_path / "in.npy"), str(tmp_path / "full.npy")) == 0
    assert _run(DESIGN, "--to", "ccm.out",
                str(tmp_path / "in.npy"), str(tmp_path / "mid.npy")) == 0
    assert _run(DESIGN, "--from", "rgb_gamma.in",
                str(tmp_path / "mid.npy"), str(tmp_path / "rest.npy")) == 0

    full = np.load(tmp_path / "full.npy")
    rest = np.load(tmp_path / "rest.npy")
    assert np.array_equal(full, rest)


def test_injection_is_just_math(tmp_path, mosaic):
    # No meaning tags: an (h, w, 3) array injected at ccm.in is accepted on
    # the strength of its shape alone -- the person injecting mid-chain is
    # trusted to know what the port eats, exactly as NumPy trusts them.
    rgbish = np.stack([mosaic, mosaic, mosaic], axis=-1)
    np.save(tmp_path / "in.npy", rgbish)
    assert _run(DESIGN, "--from", "ccm.in",
                str(tmp_path / "in.npy"), str(tmp_path / "out.npy")) == 0
    assert np.load(tmp_path / "out.npy").shape == rgbish.shape


def test_set_layer_changes_the_output_and_is_range_checked(tmp_path, mosaic,
                                                           capsys):
    np.save(tmp_path / "in.npy", mosaic)
    assert _run(DESIGN, str(tmp_path / "in.npy"), str(tmp_path / "a.npy")) == 0
    assert _run(DESIGN, "--set", "whitebalance.gain_0_0=512",
                str(tmp_path / "in.npy"), str(tmp_path / "b.npy")) == 0
    assert not np.array_equal(np.load(tmp_path / "a.npy"),
                              np.load(tmp_path / "b.npy"))

    code = _run(DESIGN, "--set", "whitebalance.gain_0_0=99999999",
                str(tmp_path / "in.npy"), str(tmp_path / "c.npy"))
    assert code == 2
    assert "outside" in capsys.readouterr().err


def test_profile_for_another_design_is_refused(tmp_path, mosaic, capsys):
    np.save(tmp_path / "in.npy", mosaic)
    code = _run("pipelines/mono/imx219/basic/pipeline.json",
                "--profile", PROFILE,
                str(tmp_path / "in.npy"), str(tmp_path / "out.npy"))
    assert code == 2
    assert "refusing" in capsys.readouterr().err


def test_png_out_and_back(tmp_path, mosaic):
    from revela.run import read_frame

    np.save(tmp_path / "in.npy", mosaic)
    assert _run(DESIGN, str(tmp_path / "in.npy"), str(tmp_path / "out.png")) == 0
    image = read_frame(tmp_path / "out.png", 10)
    assert image.shape == (48, 64, 3)
    # The PNG is the 8-bit view of the same frame: reading it back and
    # comparing the top bits against a .npy run is the round-trip check.
    assert _run(DESIGN, str(tmp_path / "in.npy"), str(tmp_path / "out.npy")) == 0
    exact = np.load(tmp_path / "out.npy")
    assert np.array_equal(image >> 2, exact >> 2)


def test_two_channel_png_is_refused(tmp_path, mosaic, capsys):
    np.save(tmp_path / "in.npy", mosaic)
    code = _run(DESIGN, "--to", "ha_green.out",
                str(tmp_path / "in.npy"), str(tmp_path / "out.png"))
    assert code == 2
    assert "2-channel" in capsys.readouterr().err


def test_explain_names_every_layer(tmp_path, mosaic, capsys):
    np.save(tmp_path / "in.npy", mosaic)
    assert _run(DESIGN, "--explain", "--set", "blacklevel.offset_0_0=-60",
                str(tmp_path / "in.npy"), str(tmp_path / "out.npy")) == 0
    output = capsys.readouterr().out
    assert "blacklevel.offset_0_0 = -60  (cli)" in output
    assert "(sensor)" in output      # the design's imx219 spoke
    assert "(default)" in output


@pytest.mark.skipif(shutil.which("verilator") is None,
                    reason="verilator not on the path")
def test_rtl_twin_is_bit_exact(tmp_path, mosaic, capsys):
    np.save(tmp_path / "in.npy", mosaic)
    assert _run(DESIGN, "--rtl",
                str(tmp_path / "in.npy"), str(tmp_path / "rtl.npy")) == 0
    assert "bit-exact" in capsys.readouterr().out

    assert _run(DESIGN, str(tmp_path / "in.npy"), str(tmp_path / "model.npy")) == 0
    assert np.array_equal(np.load(tmp_path / "rtl.npy"),
                          np.load(tmp_path / "model.npy"))


def test_console_entry_point_exists():
    import tomllib

    with open("pyproject.toml", "rb") as handle:
        table = tomllib.load(handle)
    assert table["project"]["scripts"]["revela"] == "revela.cli:main"


def test_cli_subprocess_smoke(tmp_path, mosaic):
    np.save(tmp_path / "in.npy", mosaic)
    done = subprocess.run(
        [sys.executable, "-m", "revela.cli", "run", DESIGN,
         str(tmp_path / "in.npy"), str(tmp_path / "out.npy")],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
