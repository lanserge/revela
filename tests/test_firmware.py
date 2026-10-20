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
/* the display's clock as the hardware holds it: its select and its
 * serializer's recipe, remembered as written -- --held starts them where
 * an earlier run left the board, on the 74.25 MHz row */
static uint32_t sel_mem, fb_mem, out0_mem;
static int isp_read(uint32_t a, uint32_t *v) {
    if (a == REVELA_R_CLK_SEL) { *v = sel_mem; return 0; }
    if (a == REVELA_PLL_BASE + 0x200u) { *v = fb_mem; return 0; }
    if (a == REVELA_PLL_BASE + 0x208u) { *v = out0_mem; return 0; }
    for (size_t i = 0; REVELA_IDS[i].path; i++)
        if (REVELA_IDS[i].addr == a) {
            *v = REVELA_IDS[i].value ^ (uint32_t)bad_id; return 0; }
    if (a == REVELA_PLL_BASE + 4u) { *v = 1; return 0; }   /* locked */
    *v = (a == REVELA_COMMIT) ? (uint32_t)armed : 0; return 0; }
static int isp_write(uint32_t a, uint32_t v) {
    if (armed && a < REVELA_RASTER_BASE && a != REVELA_COMMIT) {
        printf("X %u %u\n", (unsigned)a, (unsigned)v); return -1; }
    if (a == REVELA_COMMIT) armed = v && no_frames;  /* 0 releases it */
    if (a == REVELA_R_CLK_SEL) sel_mem = v;
    if (a == REVELA_PLL_BASE + 0x200u) fb_mem = v;
    if (a == REVELA_PLL_BASE + 0x208u) out0_mem = v;
    printf("W %u %u\n", (unsigned)a, (unsigned)v); return 0; }
static int mode(const struct revela_sensor_mode *m) {
    printf("S mode %s %u %u %u\n", m->name, (unsigned)m->width,
           (unsigned)m->height, (unsigned)m->bits);
    return 0; }
/* --bytes: a platform with no sensor driver, which only moves bytes */
static int poke(uint16_t reg, uint8_t value) {
    printf("S write %u %u\n", (unsigned)reg, (unsigned)value); return 0; }
static int expo(uint32_t n) { printf("S exposure %u\n", (unsigned)n); return 0; }
static int again(uint32_t n) { printf("S gain %u\n", (unsigned)n); return 0; }
static void nap(uint32_t us) { (void)us; }
static void say(const char *l) { printf("L %s\n", l); }
static struct revela_hal hal = {
    .isp_read = isp_read, .isp_write = isp_write, .sensor_mode = mode,
    .sensor_exposure = expo, .sensor_analog_gain = again,
    .sleep_us = nap, .log = say };
int main(int argc, char **argv) {
    char reply[2048];
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--bytes")) {
            hal.sensor_mode = NULL; hal.sensor_write = poke; }
    revela_fw_attach(&hal);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bytes")) continue;
        if (!strcmp(argv[i], "--bad-id")) { bad_id = 1; continue; }
        if (!strcmp(argv[i], "--no-frames")) { no_frames = 1; continue; }
        if (!strcmp(argv[i], "--held")) {
            sel_mem = 1;
            fb_mem = REVELA_CLOCKS[1].divclk | (REVELA_CLOCKS[1].mult << 8);
            out0_mem = REVELA_CLOCKS[1].out0;
            continue; }
        revela_fw_command(argv[i], reply, sizeof reply);
        printf("R %s\n", reply);
    }
    struct revela_tick t = {1};
    revela_fw_tick(&t);
    return 0; }
'''

# a frame stated by its size: the sensor module's mode of that size
FRAME_1080P30 = ("frame.width=1920 frame.height=1080 frame.bits=10 "
                 "frame.bayer_phase=2")


def _display(name="1080p30"):
    from bayerlink.rasters import raster
    r = raster(name)
    return " ".join(f"display.{f}={int(getattr(r, f))}" for f in (
        "pixel_khz", "h_total", "v_total", "h_active", "v_active", "hs_beg",
        "hs_end", "vs_beg", "vs_end", "hsync_positive", "vsync_positive"))


def _maps():
    from np2hw import pipeline as pipelines
    from np2hw.pipeline import compose
    from revela.boards.common.clocking import tmds_recipe
    from revela.boards.common.scanout import scanout_regs
    regmap = compose.register_map(pipelines.load(
        "revela.boards.pynq_z2.isp:revela_isp_core"))
    regs = scanout_regs("1080p30", 560)
    cable = {"isp": {"base": 0, "size": 0x8000, "clock_khz": 142857},
             "scanout_regs": {"base": 0x8000, "size": 0x1000,
                              "registers": regs["facts"]["offsets"],
                              "id": regs["facts"]["id"],
                              "counter_bits": [12, 11],
                              "clocks": [
                                  {"khz": khz, "ps_per_px": ps,
                                   "pll": tmds_recipe(khz)}
                                  for khz, ps in ((27000, 1400), (74250, 560),
                                                  (148500, 280))]},
             "tmds_pll": {"base": 0x9000, "size": 0x800}}
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
    one commit; then the display raster and the picture, armed. The ISP's
    geometry is its stream's, so nothing else is written. Every value to
    the register the maps state, in that order."""
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
    want.append((commit, 1))
    # the picture: the crop keeps as much of the frame as the display
    # shows, and the mark goes in its corner -- their own batch
    from revela.blocks.logo import INSET
    want += [(a["crop.x0"][0], 0), (a["crop.y0"][0], 0),
             (a["crop.w"][0], 1920), (a["crop.h"][0], 1080),
             (a["logo.x0"][0], 1920 - INSET), (a["logo.y0"][0], 1080 - INSET),
             (commit, 1)]
    r = cable["scanout_regs"]
    reg = {k: r["base"] + v for k, v in r["registers"].items()}
    from bayerlink.rasters import raster
    d = raster("1080p30")
    pll = cable["tmds_pll"]["base"]
    # the serializer's recipe lands BEFORE the clock moves (SADDR, no
    # load): the wizard reloads by itself on a lost lock, from whatever its
    # registers hold then. The clock's row is one of the raster's numbers,
    # all of them armed together -- the display switches the clock as it
    # takes them, after its frame's last line -- and the load proper comes
    # after the arm is taken, on the new clock
    want += [(pll + 0x200, 1 | 10 << 8), (pll + 0x208, 2)] * 2
    want += [(pll + 0x25C, 2), (reg["clk_sel"], 1), (reg["ps_per_px"], 560)]
    want += [(reg[f], getattr(d, f)) for f in (
        "h_total", "h_active", "hs_beg", "hs_end", "v_total", "v_active",
        "vs_beg", "vs_end")]
    want += [(reg["hs_pos"], 1), (reg["vs_pos"], 1), (reg["win_x0"], 0),
             (reg["win_y0"], 0), (reg["center"], 1), (reg["arm"], 1)]
    want += [(pll + 0x25C, 3)]
    assert writes == want
    # the sensor's mode went to the platform, and AE started it from the
    # sensor's own facts
    assert "S mode cropped_1080p 1920 1080 10" in out
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
    _, out = _run(exe, f"init {FRAME_1080P30} {_display('720p60')} "
                       "window.center=1", "config")
    config = next(l for l in out if l.startswith("R ok frame."))
    assert "display.h_total=1650" in config
    assert "frame.bayer_phase=2" in config


def test_a_mode_chosen_by_name_brings_its_frame(tmp_path):
    """The sensor module's mode, by name: the frame it delivers comes with
    it, the platform is told the mode, and the configuration reads back
    with the name first, so a replay selects it again."""
    exe, _, _ = _build(tmp_path)
    _, out = _run(exe, f"init mode=binned_640x480 {_display('480p60')} "
                       "window.center=1", "config", "modes")
    assert "R ok" in out, out
    assert "S mode binned_640x480 640 480 10" in out
    config = next(l for l in out if l.startswith("R ok mode="))
    assert config.startswith("R ok mode=binned_640x480 ")
    assert "frame.width=640 frame.height=480 frame.bits=10" in config
    assert "frame.bayer_phase=2" in config
    modes = next(l for l in out if "cropped_1080p=" in l)
    assert ("cropped_1080p=1920x1080x10,2,87500000,2416,1104" in modes
            and "binned_640x480=640x480x10,2,58333000,1852,504" in modes)


def test_a_platform_with_no_driver_has_the_modes_registers_written(
        tmp_path):
    """Where the platform only moves bytes, selecting a mode is writing its
    registers: exactly the sensor description's sequence, in order."""
    exe, _, _ = _build(tmp_path)
    _, out = _run(exe, "--bytes", f"init mode=binned_640x480 "
                                  f"{_display('480p60')} window.center=1")
    assert "R ok" in out, out
    from revela import sensors
    d = sensors.load("ov5647")
    want = [f"S write {w['address']} {w['value']}"
            for w in d["register_sequences"]["mode_binned_640x480"]["writes"]]
    assert [l for l in out if l.startswith("S write")] == want
    assert not any(l.startswith("S mode") for l in out)


def test_a_mode_the_sensor_does_not_have_is_refused(tmp_path):
    exe, _, _ = _build(tmp_path)
    writes, out = _run(exe, f"init mode=full_5mp {_display()}",
                       f"init frame.width=1280 frame.height=720 "
                       f"frame.bits=10 {_display('720p60')}")
    replies = [l for l in out if l.startswith("R ")]
    assert replies[0].startswith("R err mode full_5mp"), replies
    assert "no 1280x720 10-bit mode" in replies[1], replies


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
    """While a link comes up the ISP may have no frame to take a commit
    at, and refuses writes until it does. The firmware says the batch is
    pending and goes on to the display; and a mode change -- the ISP
    follows its stream's header -- writes the ISP only what the frame
    decides, the mark's place, after releasing the pending batch, so the
    new batch carries it and no write is refused."""
    exe, _, cable = _build(tmp_path)
    good = f"init {FRAME_1080P30} {_display()} window.center=1"
    writes, out = _run(exe, "--no-frames", good)
    assert "R ok" in out, out
    assert not any(l.startswith("X ") for l in out), "a write was refused"
    assert any("pending, until the ISP's next frame" in l for l in out)
    r = cable["scanout_regs"]
    raster = r["base"]
    # the display armed -- and, the clock switching as it is taken, the
    # serializer loaded after it, on the new clock
    pll = cable["tmds_pll"]["base"]
    assert writes[-2:] == [(raster + r["registers"]["arm"], 1),
                           (pll + 0x25C, 3)], writes[-2:]
    again, out = _run(exe, "--no-frames", good,
                      "configure frame.width=640 frame.height=480")
    replies = [l for l in out if l.startswith("R ")]
    assert replies[-1] == "R ok", out[-4:]
    assert not any(l.startswith("X ") for l in out), "a write was refused"
    after = again[len(writes):]
    from revela.blocks.logo import INSET
    a = _addr(_maps()[0])
    commit = a["pipe.commit"][0]
    isp = [w for w in after if w[0] < raster]
    assert isp == [(commit, 0), (a["crop.x0"][0], 0), (a["crop.y0"][0], 0),
                   (a["crop.w"][0], 640), (a["crop.h"][0], 480),
                   (a["logo.x0"][0], 640 - INSET),
                   (a["logo.y0"][0], 480 - INSET), (commit, 1)], isp


def test_ae_starts_inside_the_frame_it_is_given(tmp_path):
    """The sensor's default integration was cut for its default frame; the
    caller's limit -- the longest integration the frame holds, as whoever
    knows the frame says -- clamps it, or the frame would stretch."""
    exe, _, _ = _build(tmp_path)
    _, out = _run(exe, f"init {FRAME_1080P30} {_display()} window.center=1 "
                       "aaa.ae=1 aaa.exposure_max=496")
    assert "S exposure 496" in out


def test_a_raster_at_a_clock_the_display_does_not_make_is_refused(tmp_path):
    """The board's display makes the clocks of its table: a raster at
    another is refused before anything is written, the status lists them,
    and a raster at one of them selects its row and its fine steps."""
    exe, _, _ = _build(tmp_path)
    writes, out = _run(exe, f"init {FRAME_1080P30} {_display('vga')} "
                            "window.center=1", "status")
    assert writes == []
    assert any("makes no 25175 kHz clock" in l for l in out)
    assert any("tv_khz=27000,74250,148500" in l for l in out)
    # and the clock the ISP takes its samples on, for whoever times a source
    assert any(" isp_khz=142857 " in l for l in out)
    # and the longest raster the display scans, for whoever plans one
    assert any(" counters=12x11 " in l for l in out)
    # 480p's clock is a row: its raster selects it
    writes, out = _run(exe, f"init {FRAME_1080P30} {_display('480p60')} "
                            "window.center=1")
    r = _maps()[1]["scanout_regs"]
    assert (r["base"] + r["registers"]["clk_sel"], 0) in writes
    assert (r["base"] + r["registers"]["ps_per_px"], 1400) in writes


def test_a_1080p60_raster_selects_the_third_row_and_its_pll_recipe(tmp_path):
    """148.5 MHz is row 2: the select says 2, the serializer MMCM is given
    multiplier 5 and divider 1 -- five times the pixel clock -- and the
    fine steps are that row's."""
    exe, _, cable = _build(tmp_path)
    writes, out = _run(exe, f"init {FRAME_1080P30} {_display('1080p60')} "
                            "window.center=1")
    assert "R ok" in out, out
    r, pll = cable["scanout_regs"], cable["tmds_pll"]["base"]
    reg = {k: r["base"] + v for k, v in r["registers"].items()}
    assert (reg["clk_sel"], 2) in writes
    assert writes.count((pll + 0x200, 1 | 5 << 8)) == 2
    assert writes.count((pll + 0x208, 1)) == 2
    assert (reg["ps_per_px"], 280) in writes


def test_a_struct_that_moves_changes_the_contract(tmp_path, monkeypatch):
    """A contract is its header's number and the layout of everything that
    header includes: the core provides exactly what the loops and sensor
    module require, and a struct changed in an included header -- under a
    number nobody bumped -- is a different contract, so a module built on
    the old layout is refused rather than run."""
    import json
    regmap, cable = _maps()
    core = json.loads(package.core_package(regmap, cable, {}, "0.1")
                      ["manifest.json"])
    loop = json.loads(package.loop_package("ae", "0.1")["manifest.json"])
    sensor = json.loads(package.sensor_package("ov5647", None, "0.1")
                        ["manifest.json"])
    assert set(loop["requires"]) <= set(core["provides"])
    assert set(sensor["requires"]) <= set(core["provides"])
    before = package.api("revela_algo.h", "REVELA_ALGO_API")
    for h in FW.glob("*.h"):
        shutil.copy(h, tmp_path / h.name)
    fw = tmp_path / "revela_fw.h"
    fw.write_text(fw.read_text().replace("uint32_t bits;",
                                         "uint32_t bits, pad;", 1))
    monkeypatch.setattr(package, "HERE", tmp_path)
    after = package.api("revela_algo.h", "REVELA_ALGO_API")
    assert before.split("-")[0] == after.split("-")[0]
    assert before != after


def test_a_frame_bigger_than_the_display_shows_its_middle(tmp_path):
    """Full resolution on a 1080p display: the crop keeps the display's
    area from the middle of the frame, and the mark lands in the corner of
    what it keeps."""
    from revela.blocks.logo import INSET
    exe, regmap, _ = _build(tmp_path)
    writes, out = _run(exe, f"init mode=full_2592x1944 {_display('1080p30')}"
                            " window.center=1")
    assert "R ok" in out, out
    a = _addr(regmap)
    keys = ("crop.x0", "crop.y0", "crop.w", "crop.h", "logo.x0", "logo.y0")
    got = {k: v for k in keys for addr, v in writes if addr == a[k][0]}
    assert got == {"crop.x0": (2592 - 1920) // 2, "crop.y0": (1944 - 1080) // 2,
                   "crop.w": 1920, "crop.h": 1080,
                   "logo.x0": 1920 - INSET, "logo.y0": 1080 - INSET}, got


def test_a_clock_the_display_already_runs_is_not_reloaded(tmp_path):
    """A fresh firmware -- every run of its platform starts one -- finding
    the board already on the clock it asks for leaves the serializer
    alone: reloading it stops the HDMI clock, and the TV takes seconds to
    find the signal again. The raster is still written and armed, the
    clock's row among its numbers -- the row it already runs, so nothing
    switches. On another clock, the switch is made: the new row armed with
    the raster, the serializer staged before and loaded after."""
    exe, _, cable = _build(tmp_path)
    r = cable["scanout_regs"]
    reg = {k: r["base"] + v for k, v in r["registers"].items()}
    pll = cable["tmds_pll"]["base"]
    serializer = {pll + 0x200, pll + 0x208, pll + 0x25C}
    good = f"init {FRAME_1080P30} {_display('1080p30')} window.center=1"
    writes, out = _run(exe, "--held", good)
    assert "R ok" in out, out
    assert not [w for w in writes if w[0] in serializer], writes
    assert [w for w in writes if w[0] == reg["clk_sel"]] == \
        [(reg["clk_sel"], 1)], writes
    assert (reg["arm"], 1) in writes and (reg["h_total"], 2200) in writes
    writes, out = _run(exe, "--held",
                       f"init {FRAME_1080P30} {_display('1080p60')} "
                       "window.center=1")
    assert "R ok" in out, out
    at = {w: i for i, w in enumerate(writes)}
    staged, row = at[(pll + 0x25C, 2)], at[(reg["clk_sel"], 2)]
    armed, loaded = at[(reg["arm"], 1)], at[(pll + 0x25C, 3)]
    assert staged < row < armed < loaded, (staged, row, armed, loaded)

