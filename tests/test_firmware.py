# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The firmware, run against a fake platform.

The core, the 3A stubs and a sensor module are compiled as they ship,
with a HAL that records every write and answers every read; the actions
are driven through the console, as a Pi's panel or an MCU's host drives
them. What is checked is what reaches the hardware, in order.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest

from revela.firmware import HERE as FW
from revela.firmware import package

pytest.importorskip("bayerlink")
CC = shutil.which("cc")
pytestmark = pytest.mark.skipif(CC is None, reason="needs a C compiler")

FAKE = r'''
#include <stdio.h>
#include <string.h>
#include "revela_fw.h"
#include "core_map.h"
/* --no-frames: the ISP takes no commit, and -- as the real one does --
 * refuses its registers' writes while one is pending */
static int bad_id, no_frames, armed;
static int isp_read(uint32_t a, uint32_t *v) {
    for (size_t i = 0; REVELA_IDS[i].path; i++)
        if (REVELA_IDS[i].addr == a) {
            *v = REVELA_IDS[i].value ^ (uint32_t)bad_id; return 0; }
    *v = (a == REVELA_COMMIT) ? (uint32_t)armed : 0; return 0; }
static int isp_write(uint32_t a, uint32_t v) {
    if (armed && a < REVELA_RASTER_BASE && a != REVELA_COMMIT) {
        printf("X %u %u\n", (unsigned)a, (unsigned)v); return -1; }
    if (a == REVELA_COMMIT) armed = v && no_frames;  /* 0 releases it */
    printf("W %u %u\n", (unsigned)a, (unsigned)v); return 0; }
static int mode(uint32_t w, uint32_t h, uint32_t b, uint32_t l, uint32_t f) {
    printf("S mode %u %u %u %u %u\n", (unsigned)w, (unsigned)h, (unsigned)b,
           (unsigned)l, (unsigned)f); return 0; }
static int expo(uint32_t n) { printf("S exposure %u\n", (unsigned)n); return 0; }
static int again(uint32_t n) { printf("S gain %u\n", (unsigned)n); return 0; }
static void nap(uint32_t us) { (void)us; }
static void say(const char *l) { printf("L %s\n", l); }
static const struct revela_hal hal = {
    .isp_read = isp_read, .isp_write = isp_write, .sensor_mode = mode,
    .sensor_exposure = expo, .sensor_analog_gain = again,
    .sleep_us = nap, .log = say };
int main(int argc, char **argv) {
    char reply[2048];
    revela_fw_attach(&hal);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bad-id")) { bad_id = 1; continue; }
        if (!strcmp(argv[i], "--no-frames")) { no_frames = 1; continue; }
        revela_fw_command(argv[i], reply, sizeof reply);
        printf("R %s\n", reply);
    }
    struct revela_tick t = {1};
    revela_fw_tick(&t);
    return 0; }
'''

FRAME_1080P30 = ("frame.width=1920 frame.height=1080 frame.bits=12 "
                 "frame.bayer_phase=1 frame.line_ns=29630 "
                 "frame.frame_lines=1125")


def _display(name="1080p30"):
    from bayerlink.rasters import raster
    r = raster(name)
    return " ".join(f"display.{f}={int(getattr(r, f))}" for f in (
        "pixel_khz", "h_total", "v_total", "h_active", "v_active", "hs_beg",
        "hs_end", "vs_beg", "vs_end", "hsync_positive", "vsync_positive"))


def _maps():
    from np2hw import pipeline as pipelines
    from np2hw.pipeline import compose
    from revela.boards.common.scanout import scanout_regs
    regmap = compose.register_map(pipelines.load(
        "revela.boards.pynq_z2.isp:revela_isp_core"))
    regs = scanout_regs("1080p30", 560)
    cable = {"isp": {"base": 0, "size": 0x8000},
             "scanout_regs": {"base": 0x8000, "size": 0x1000,
                              "registers": regs["facts"]["offsets"],
                              "id": regs["facts"]["id"],
                              "counter_bits": [12, 11]}}
    return regmap, cable


def _sensor():
    from np2hw.design import profile
    from revela import sensors
    fitted = profile.resolve("revela.sensors.ov5647.profile:ov5647")
    values = {f"{fitted.for_ref}/{k}": v
              for k, v in profile.relative(fitted).items()}
    return fitted, values, sensors.load("ov5647")


def _build(tmp_path, design=None, sensor=True, loops=("ae", "awb", "af")):
    regmap, cable = _maps()
    for h in package.HEADERS:
        shutil.copy(FW / h, tmp_path / h)
    (tmp_path / "core_map.h").write_text(
        package.core_map_h(regmap, cable, design))
    (tmp_path / "fake.c").write_text(FAKE)
    sources = [FW / "core" / "core.c", tmp_path / "fake.c"]
    sources += [FW / loop / f"{loop}.c" for loop in loops]
    if sensor:
        _, values, description = _sensor()
        (tmp_path / "sensor.c").write_text(
            package.sensor_c("ov5647", values, description))
        sources.append(tmp_path / "sensor.c")
    # an absent module is a weak symbol nothing defines: NULL on Linux,
    # and on macOS only once the linker is told it may stay undefined
    absent = [f"revela_{loop}" for loop in package.LOOPS if loop not in loops]
    absent += [] if sensor else ["revela_sensor"]
    flags = ([f"-Wl,-U,_{name}" for name in absent]
             if sys.platform == "darwin" else [])
    exe = tmp_path / "fw"
    subprocess.run([CC, "-std=c99", "-Wall", "-Wextra", "-Werror",
                    f"-I{tmp_path}", *flags, "-o", str(exe),
                    *map(str, sources)],
                   check=True, capture_output=True, text=True)
    return exe, regmap, cable


def _run(exe, *commands):
    out = subprocess.run([str(exe), *commands], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    writes = [tuple(map(int, l.split()[1:3])) for l in out if l[:2] == "W "]
    return writes, out


def _addr(regmap):
    return {f"{b['path']}.{r['name']}": (r["address"], r["bits"])
            for b in regmap["blocks"] for r in b["registers"]}


def test_init_loads_the_build_then_applies_the_configuration(tmp_path):
    """The design's settings, then the sensor's profile at its anchors,
    one commit; the ISP's window, its commit; the display raster and the
    picture, armed. Every value to the register the maps state, in that
    order, and nothing else."""
    from np2hw.design import profile
    design = {"rgb_gamma.knots_1": 60}
    exe, regmap, cable = _build(tmp_path, design)
    fitted, _, _ = _sensor()
    writes, out = _run(exe, f"init {FRAME_1080P30} {_display()} "
                            "window.center=1 aaa.ae=1")
    assert "R ok" in out, out
    a = _addr(regmap)
    commit = a["pipe.commit"][0]

    def raw(key, value):
        addr, bits = a[key]
        return addr, value & ((1 << bits) - 1)
    want = [raw(k, v) for k, v in design.items()]
    want += [raw(k, v) for k, v in profile.flatten(fitted, regmap).items()]
    want += [(a["pipe.window_x0"][0], 0), (a["pipe.window_y0"][0], 0),
             (a["pipe.window_x1"][0], 1920), (a["pipe.window_y1"][0], 1080),
             (commit, 1)]
    r = cable["scanout_regs"]
    reg = {k: r["base"] + v for k, v in r["registers"].items()}
    from bayerlink.rasters import raster
    d = raster("1080p30")
    want += [(reg[f], getattr(d, f)) for f in (
        "h_total", "h_active", "hs_beg", "hs_end", "v_total", "v_active",
        "vs_beg", "vs_end")]
    want += [(reg["hs_pos"], 1), (reg["vs_pos"], 1), (reg["win_x0"], 0),
             (reg["win_y0"], 0), (reg["win_w"], 1920), (reg["win_h"], 1080),
             (reg["center"], 1), (reg["arm"], 1)]
    assert writes == want
    # the sensor's mode went to the platform, and AE started it from the
    # sensor's own facts
    assert "S mode 1920 1080 12 29630 1125" in out
    assert any(l.startswith("S gain ") for l in out)


def test_a_bitstream_of_another_build_is_not_written(tmp_path):
    exe, _, _ = _build(tmp_path)
    writes, out = _run(exe, "--bad-id",
                       f"init {FRAME_1080P30} {_display()} window.center=1")
    assert writes == []
    assert any("not the build its map came from" in l for l in out)


def test_a_configuration_that_cannot_be_applied_writes_nothing(tmp_path):
    """A window larger than the display is refused before a register is
    touched -- the running configuration stays as it was."""
    exe, _, _ = _build(tmp_path)
    good = f"init {FRAME_1080P30} {_display()} window.center=1"
    before, _ = _run(exe, good)
    writes, out = _run(exe, good, "configure window.width=4000")
    assert writes == before
    assert any("larger than the 1920x1080 display" in l for l in out)


def test_the_configuration_reads_back_as_init_takes_it(tmp_path):
    exe, _, _ = _build(tmp_path)
    _, out = _run(exe, f"init {FRAME_1080P30} {_display('1080p60')} "
                       "window.center=1", "config")
    config = next(l for l in out if l.startswith("R ok frame."))
    assert "display.pixel_khz=148500" in config
    assert "frame.bayer_phase=1" in config


def test_3a_loops_run_only_where_present_and_on(tmp_path):
    exe, _, _ = _build(tmp_path, loops=("awb",))
    _, out = _run(exe, f"init {FRAME_1080P30} {_display()} window.center=1 "
                       "aaa.ae=1 aaa.awb=1", "status")
    status = next(l for l in out if l.startswith("R ok build="))
    assert "ae=absent" in status and "awb=on" in status
    assert "af=absent" in status
    assert not any(l.startswith("S gain") for l in out)


def test_the_firmware_is_portable_c(tmp_path):
    """Nothing in the firmware reaches an operating system: no POSIX, no
    Linux, nothing a bare MCU's C library lacks."""
    for path in [FW / "core" / "core.c", *(FW / l / f"{l}.c"
                                           for l in package.LOOPS),
                 *(FW / h for h in package.HEADERS)]:
        includes = re.findall(r"#include [<\"]([^>\"]+)", path.read_text())
        system = [i for i in includes if not i.startswith("revela_")
                  and i != "core_map.h"]
        assert set(system) <= {"stdint.h", "stddef.h", "stdarg.h",
                               "stdio.h", "stdlib.h", "string.h"}, (
            path.name, system)


def test_each_part_packages_for_its_slot(tmp_path):
    regmap, cable = _maps()
    core = package.core_package(regmap, cable, {}, "0.1")
    assert {"core.c", "core_map.h", "pi_glue.c", "regmap.json",
            "cable_map.json", *package.HEADERS} <= set(core)
    for loop in package.LOOPS:
        files = package.loop_package(loop, "0.1")
        assert f"{loop}.c" in files and '"slot": "' + loop in files[
            "manifest.json"]
    sensor = package.sensor_package("ov5647", None, "0.1")
    assert "sensor_ov5647.c" in sensor


def test_a_commit_the_isp_has_not_taken_yet_is_not_a_refusal(tmp_path):
    """While a mode changes or a link comes up the ISP may have no frame to
    take a commit at, and refuses writes until it does. The settings and
    the window go in ONE batch under one commit, so nothing is refused;
    the firmware says the batch is pending and goes on to the display. A
    new configuration releases the untaken batch -- the ISP takes a commit
    only at the end of a frame of its current window, which after a mode
    change may never come -- and commits its own, the window with it."""
    exe, _, cable = _build(tmp_path)
    good = f"init {FRAME_1080P30} {_display()} window.center=1"
    writes, out = _run(exe, "--no-frames", good)
    assert "R ok" in out, out
    assert not any(l.startswith("X ") for l in out), "a write was refused"
    assert any("pending, until the ISP's next frame" in l for l in out)
    r = cable["scanout_regs"]
    assert writes[-1] == (r["base"] + r["registers"]["arm"], 1)
    again, out = _run(exe, "--no-frames", good,
                      "configure frame.width=640 frame.height=480")
    replies = [l for l in out if l.startswith("R ")]
    assert replies[-1] == "R ok", out[-4:]
    assert not any(l.startswith("X ") for l in out), "a write was refused"
    assert any("untaken batch released" in l for l in out)


def test_ae_starts_inside_the_frame_it_is_given(tmp_path):
    """The sensor's default integration was cut for its default frame; a
    shorter frame -- a faster mode -- clamps it, by the sensor's own
    margin and by the caller's limit, or the frame would stretch."""
    exe, _, _ = _build(tmp_path)
    frame = FRAME_1080P30.replace("frame.frame_lines=1125",
                                  "frame.frame_lines=504")
    _, out = _run(exe, f"init {frame} {_display()} window.center=1 aaa.ae=1")
    lines = [int(l.split()[2]) for l in out if l.startswith("S exposure")]
    assert lines and lines[0] <= 504 - 4
    _, out = _run(exe, f"init {FRAME_1080P30} {_display()} window.center=1 "
                       "aaa.ae=1 aaa.exposure_max=496")
    assert "S exposure 496" in out
