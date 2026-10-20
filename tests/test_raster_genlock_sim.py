# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The raster following a stream, simulated: a frame start arriving off
its target is answered by fine-phase steps the right way round, more for
a larger error, spread over the frames after it; one beyond steering by
a hold for the next frame start, then a cooled raster that holds no more
-- and never a line or a frame cut short."""
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


def test_the_raster_steers_by_the_arrival_error_and_holds_once(tmp_path):
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    str(COMMON / "sim" / "tb_raster_genlock.v"),
                    str(COMMON / "hdl" / "raster.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=600)
    assert "genlock: done" in run.stdout, run.stdout[-3000:]
    got = {int(n): (int(s), int(r), int(h), int(c)) for n, s, r, h, c in
           re.findall(r"ARRIVAL (\d+) at \S+: steps=(\d+) retard=(\d+) "
                      r"holds=(\d+) cuts=(\d+)", run.stdout)}

    steps = {n: got[n][0] for n in got}
    retard = {n: got[n][1] for n in got}
    # late: retarded, every step; early: advanced; a larger error, more
    assert steps[1] > 0 and retard[1] == steps[1]       # 40 px late
    assert steps[2] > 0 and retard[2] == 0              # 70 px early
    assert retard[3] == steps[3] > steps[1]             # 105 px late
    assert steps[4] < PS                                # one pixel: less
    assert [got[n][2] for n in range(1, 7)] == [0, 0, 0, 0, 1, 0]  # holds
    assert all(got[n][3] == 0 for n in got)             # never a cut


def _smooth(tmp_path, follow=1, drift=20, start=0, frames=160, quiet=0,
            jump_at=-1, jump_px=0):
    """The closed-loop bench: each frame's arrival error (pixels), the
    steps the raster made over it (positive retards), the cuts so far, the
    closest and widest gaps between its steps, how many of the stream's
    pixels reached the sink, whether the first of them was astray of the
    window's first pixel, and whether the raster held; and the clock the
    stream's first pixel reached the sink on."""
    vvp = tmp_path / f"smooth_{follow}_{drift}_{start}_{quiet}_{jump_at}.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp),
                    f"-Ptb_raster_smooth.FOLLOW={follow}",
                    f"-Ptb_raster_smooth.DRIFT={drift}",
                    f"-Ptb_raster_smooth.START={start}",
                    f"-Ptb_raster_smooth.FRAMES={frames}",
                    f"-Ptb_raster_smooth.QUIET={quiet}",
                    f"-Ptb_raster_smooth.JUMP_AT={jump_at}",
                    f"-Ptb_raster_smooth.JUMP_PX={jump_px}",
                    str(COMMON / "sim" / "tb_raster_smooth.v"),
                    str(COMMON / "hdl" / "raster.v")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True,
                         timeout=600)
    assert "smooth: done" in run.stdout, run.stdout[-3000:]
    rows = [tuple(int(v) for v in row) for row in re.findall(
        r"FRAME \d+ err=(-?\d+) steps=(-?\d+) cuts=(\d+) gap=(\d+)\.\.(\d+) "
        r"shown=(\d+) astray=(\d+) held=(\d+)", run.stdout)]
    first = int(re.search(r"FIRST SHOWN at (-?\d+)", run.stdout).group(1))
    return rows, first


# the bench's frame, in clocks
FRAME = H_TOTAL * 50


def test_the_smooth_raster_steers_a_constant_trim_spread_evenly(tmp_path):
    """A source gaining 20 steps a frame is followed by 20 steps a frame
    the other way -- the crystals' difference learned -- each frame's
    steps evenly spaced across it, the error held within a pixel, and
    never a cut: what a television sees is a clock a little off nominal,
    constant."""
    rows, _ = _smooth(tmp_path, drift=20, frames=220)
    assert all(r[2] == 0 for r in rows)
    for err, steps, _, lo, hi, *_ in rows[-30:]:
        assert abs(err) <= 2, rows[-30:]
        assert steps == -20, rows[-30:]
        assert hi - lo <= 1 and lo >= 16, rows[-30:]   # 5000 / 20 = 250


def test_a_raster_starts_aligned_and_shows_only_whole_frames(tmp_path):
    """From reset the raster holds until the stream's first frame start,
    then takes as many lines as put the next on the target. The genlock
    locks on four frame starts in a row at the window's first line, and
    only then is a frame shown -- decided at its first pixel, so all of it
    -- and every frame after; the error left to steer is under a line, and
    no line or frame on the cable is ever cut short."""
    rows, _ = _smooth(tmp_path, drift=-20, start=150)
    assert all(r[2] == 0 for r in rows)                 # no cut, ever
    held = [i for i, r in enumerate(rows) if r[7]]
    assert held == [0, 1], held                          # the start's hold
    assert all(r[5] == 0 for r in rows[:6]), rows[:7]    # hold, then lock
    assert all(r[5] > 0 and r[6] == 0 for r in rows[6:]), rows[:8]
    assert max(abs(r[0]) for r in rows[2:]) < H_TOTAL
    assert all(abs(e) <= 2 and st == 20 for e, st, *_ in rows[-30:]), rows[-30:]


def test_a_phase_jump_holds_a_frame_end_and_comes_back_whole(tmp_path):
    """The source's frame start jumping 17 lines -- past what steps can
    pull in -- is answered by finishing the frame on the cable, holding at
    its end for the next frame start, and coming back aligned: frames go
    dark rather than show from their middle, and nothing is cut."""
    rows, _ = _smooth(tmp_path, frames=180, jump_at=40, jump_px=1700)
    assert all(r[2] == 0 for r in rows)
    held = [i for i, r in enumerate(rows) if r[7]]
    assert held[:2] == [0, 1] and len(held) == 4, held
    jump = held[2:]
    assert jump[1] == jump[0] + 1 and jump[0] > 40, held  # one hold, after
    assert all(r[6] == 0 for r in rows)                   # never astray
    assert all(r[5] == 0 for r in rows[jump[0]:jump[1] + 5])  # hold, lock
    assert all(r[5] > 0 for r in rows[jump[1] + 5:])
    assert all(abs(e) <= 2 for e, *_ in rows[-10:]), rows[-10:]


def test_a_stream_that_does_not_fit_the_raster_shows_nothing(tmp_path):
    """A source at twice the raster's rate -- a source that has changed its
    rate before the raster's mode follows -- puts two frame starts in each
    raster frame: never four in a row on the window's first line, so the
    genlock never locks and not one pixel of it is shown, while nothing on
    the cable is cut."""
    rows, first = _smooth(tmp_path, drift=FRAME * PS // 2, frames=40)
    assert first < 0 and all(r[5] == 0 for r in rows), rows[:8]
    assert all(r[2] == 0 for r in rows)


def test_follow_off_neither_steps_nor_holds(tmp_path):
    """follow off: the raster runs free -- a source drifting away and a
    frame start five lines off are answered by no step and no hold, and
    still a frame is shown whole or not at all."""
    rows, _ = _smooth(tmp_path, follow=0, drift=200, start=500, frames=20)
    assert all(st == 0 and c == 0 and h == 0 and a == 0
               for _, st, c, _, _, _, a, h in rows), rows


def test_a_new_raster_paints_the_fill_while_the_television_finds_it(tmp_path):
    """G_QUIET_STEPS of time after the raster starts -- counted from
    reset, its hold included -- show none of the stream: a television
    trains on a dark picture at once and on a dense one in minutes. The
    first pixel shown comes on the first frame past that time, and every
    frame after shows, as every frame does with no quiet at all, once the
    genlock has locked."""
    loud, loud_first = _smooth(tmp_path, quiet=0, frames=16)
    rows, first = _smooth(tmp_path, quiet=10, frames=16)
    assert loud_first < 7 * FRAME
    assert 10 * FRAME <= first < 11 * FRAME + 11 * H_TOTAL, first
    shown = [r[5] for r in rows]
    start = next(i for i, n in enumerate(shown) if n)
    assert all(n > 0 for n in shown[start:]), shown
    assert all(r[2] == 0 for r in rows)
