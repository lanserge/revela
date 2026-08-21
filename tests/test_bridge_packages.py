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
