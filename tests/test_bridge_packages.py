# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The uploadable packages: what revela ships to a picam2hdmi bridge.

The ISP package is the register map twice over -- once as the C key
table the module writes through, once as the verbatim regmap.json the
panel renders widgets from -- and both copies are cut from one input
in one breath, which is the only arrangement under which two copies
are not two sources of truth.
"""
from __future__ import annotations

import io
import json
import sys
import tarfile

from conftest import chain, describe
from revela import designs
from revela.host import bridge_module


def _regmap():
    return designs.build(describe(
        "pkg_probe", chain("blacklevel", "whitebalance"),
        bit_depth=10)).register_map()


def test_generated_c_speaks_the_command_vocabulary():
    """set/get/commit is the whole panel protocol; the module must
    answer all three and refuse the rest with a usable message."""
    c = bridge_module.generate_isp_c(_regmap())
    assert "bridge_command_hook" in c
    for verb in ('"set %63s %lld"', '"get %63s"', '"commit"'):
        assert verb in c, f"the hook does not parse {verb}"
    assert "err unknown command" in c


def test_package_carries_the_map_verbatim(tmp_path, monkeypatch):
    """The panel's widgets and the module's writes must come from ONE
    artifact: the regmap rides the package byte-comparable to the
    input, beside the C generated from it."""
    regmap = _regmap()
    map_path = tmp_path / "in_regmap.json"
    map_path.write_text(json.dumps(regmap, indent=2))
    out = tmp_path / "pkg.tar.gz"
    monkeypatch.setattr(sys, "argv", [
        "bridge_module", "--regmap", str(map_path), "--out", str(out)])
    assert bridge_module.main() == 0

    with tarfile.open(fileobj=io.BytesIO(out.read_bytes())) as tar:
        names = {m.name for m in tar.getmembers()}
        assert {"manifest.json", "revela_isp.c", "regmap.json"} <= names
        shipped = json.loads(tar.extractfile("regmap.json").read())
    assert shipped == regmap

    # every key in the shipped map is a key the C table can write
    with tarfile.open(fileobj=io.BytesIO(out.read_bytes())) as tar:
        c = tar.extractfile("revela_isp.c").read().decode()
    for block in shipped["blocks"]:
        for register in block["registers"]:
            assert f'"{block["path"]}.{register["name"]}"' in c


def _run_link_up(tmp_path, regmap, design, sensor, ids):
    """Compile the ISP package against a stub bridge, bring the link up,
    and return (the register writes, in order, as (address, raw value);
    what it printed). ``ids`` is what the far end's ID words read as;
    ``sensor`` the scoped profile entries of a sensor package, or None
    for none installed. (A real sensor package's C needs Linux's V4L2
    headers, so the driver is supplied here from the same keys.)"""
    import shutil
    import subprocess

    import pytest

    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    root = tmp_path / "modules" / "isp"
    root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "bridge.h").write_text(
        "#include <stddef.h>\n#include <stdint.h>\n"
        "struct bridge_frame { uint32_t seq; double phase_lines; int locked;"
        " int sensor_fd; };\n"
        "struct bridge_link { int up; const uint8_t *edid; };\n"
        "int bridge_ddc_write(uint16_t reg, uint32_t value);\n"
        "int bridge_ddc_read(uint16_t reg, uint32_t *value);\n")
    (root / "revela_sensor_api.h").write_text(bridge_module.SENSOR_API_H)
    (root / "revela_algo_api.h").write_text(bridge_module.ALGO_API_H)
    (root / "isp.c").write_text(bridge_module.generate_isp_c(regmap, design))
    reads = "".join(f"    if (reg == {a}) {{ *value = {v}u; return 0; }}\n"
                    for a, v in ids.items())
    if sensor is None:
        driver = ""
    else:
        entries = "".join(f'    {{ "{k}", {v} }},\n' for k, v in sensor.items())
        driver = (
            "static const struct revela_profile_entry profile[] = {\n"
            f"{entries}    {{ 0, 0 }} }};\n"
            "static const struct revela_sensor_driver drv = {\n"
            "    .api = REVELA_SENSOR_API, .name = \"probe\", "
            ".profile = profile };\n"
            "const struct revela_sensor_driver *revela_sensor(void) "
            "{ return &drv; }\n")
    (root / "harness.c").write_text(f"""
#include <stdio.h>
#include <string.h>
#include "../../bridge.h"
#include "revela_sensor_api.h"
#include "revela_algo_api.h"
int bridge_ddc_write(uint16_t reg, uint32_t value) {{
    printf("W %u %u\\n", reg, value); return 0; }}
int bridge_ddc_read(uint16_t reg, uint32_t *value) {{
{reads}    *value = 0; return 0; }}
{driver}const struct revela_algo *revela_algo(void) {{ return 0; }}
void bridge_link_hook(const struct bridge_link *l);
int main(void) {{
    uint8_t edid[256] = {{0}}; memcpy(edid + 80, "BAYERLINK", 9);
    struct bridge_link l = {{ 1, edid }};
    bridge_link_hook(&l);
    return 0; }}
""")
    exe = tmp_path / "probe"
    flags = [] if sensor is not None else ["-Wl,-U,_revela_sensor"] \
        if sys.platform == "darwin" else []
    subprocess.run([cc, "-Wall", "-Werror", *flags, "-o", str(exe),
                    str(root / "isp.c"), str(root / "harness.c")],
                   check=True, capture_output=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True,
                         text=True).stdout
    writes = [tuple(map(int, line.split()[1:3]))
              for line in out.splitlines() if line.startswith("W ")]
    return writes, out


def _board():
    from revela.designs import profile
    regmap = designs.load(
        "revela.boards.pynq_z2.isp:revela_isp_core").register_map()
    design = profile.flatten(
        profile.resolve("revela.boards.pynq_z2.isp:display"), regmap)
    fitted = profile.resolve("revela.sensors.ov5647.profile:ov5647")
    sensor = {f"{fitted.for_ref}/{k}": v
              for k, v in profile.relative(fitted).items()}
    ids = {b["id_version"]["address"]: b["id_version"]["value"]
           for b in regmap["blocks"]}
    address = {f"{b['path']}.{r['name']}": (r["address"], r["bits"])
               for b in regmap["blocks"] for r in b["registers"]}

    def raw(key, value):
        a, bits = address[key]
        return a, value & ((1 << bits) - 1)
    return regmap, design, fitted, sensor, ids, address, raw


def test_link_up_writes_the_design_then_the_sensor_at_their_anchors(tmp_path):
    """At link-up the package writes the design's own settings, then the
    sensor's tuning anchored wherever the sensor's composite sits --
    every value to the register the map says, nothing else."""
    from revela.designs import profile
    regmap, design, fitted, sensor, ids, address, raw = _board()
    writes, _ = _run_link_up(tmp_path, regmap, design, sensor, ids)
    expected = [raw(k, v) for k, v in design.items()]
    expected += [raw(k, v) for k, v in profile.flatten(fitted, regmap).items()]
    expected.append((address["pipe.commit"][0], 1))
    assert writes == expected
    # The anchors are real: the curve at the design's level, the tuning
    # inside the ISP composite.
    assert any(k.startswith("rgb_gamma.") for k in design)
    assert all(k.startswith("isp.") for k in profile.flatten(fitted, regmap))


def test_a_bitstream_that_is_not_this_build_is_left_untouched(tmp_path):
    """One block's ID word disagreeing with the map is enough: nothing is
    written, and the package says which block and what it read."""
    regmap, design, _, sensor, ids, _, _ = _board()
    blacklevel = next(b for b in regmap["blocks"]
                      if b["path"] == "isp.blacklevel")["id_version"]
    ids[blacklevel["address"]] = blacklevel["value"] ^ 1
    writes, out = _run_link_up(tmp_path, regmap, design, sensor, ids)
    assert writes == []
    assert "isp.blacklevel reads ID" in out and "nothing written" in out


def test_the_design_settings_are_written_with_no_sensor_package(tmp_path):
    """The display curve is the ISP package's own; a camera without a
    sensor package still gets it."""
    regmap, design, _, _, ids, address, raw = _board()
    writes, out = _run_link_up(tmp_path, regmap, design, None, ids)
    assert writes == [raw(k, v) for k, v in design.items()] + \
        [(address["pipe.commit"][0], 1)]
    assert "design's settings only" in out
