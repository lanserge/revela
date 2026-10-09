# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A GPIO block from one declaration of its fields: the fabric and the
layout software reads are both derived from it."""
import importlib.util
from pathlib import Path

import pytest

from revela.boards.zynq7 import gpio

CONTROL = {"gpio": "ctrl", "parameters": {"capture": "is there a consumer"},
           "channels": [{"direction": "out", "fields": [
               {"name": "reset", "cell": "s_reset", "default": 0},
               {"name": "pick", "cell": "s_pick", "default": 1, "when": "capture"},
               {"skip": 1},
               {"name": "mode", "bits": 2, "cell": "s_mode", "default": 2}]}]}
STATUS = {"gpio": "stat", "channels": [{"direction": "in", "cell": "cat", "fields": [
    {"name": "ok"},
    {"group": "sub", "fields": [{"name": "code", "bits": 3}, {"pad": 4, "cell": "zero"}]},
    {"name": "count", "bits": 8}]}]}


def test_the_control_word_is_derived_from_its_fields():
    block = gpio.words(CONTROL)
    # width from the fields; power-on value from their defaults: pick (bit
    # 1) and mode = 2 at bits 3..4
    assert block["nodes"]["ctrl"]["build"] == {
        "width": 5, "out": 1, "default": (1 << 1) | (2 << 3)}
    assert block["nodes"]["s_mode"]["build"] == {"IN": 5, "HI": 4, "LO": 3}
    assert block["nodes"]["s_pick"]["when"] == "capture"
    assert [p["name"] for p in block["outputs"]] == ["reset", "pick", "mode"]


def test_the_status_word_is_packed_low_bit_first():
    block = gpio.words(STATUS)
    assert block["nodes"]["stat"]["build"] == {"width": 1 + 3 + 4 + 8}
    # each word's width stated: ok, the group (code + pad), count
    assert block["nodes"]["cat"]["build"] == {"widths": [1, 7, 8]}
    assert block["nodes"]["zero"]["build"] == {"WIDTH": 4, "VALUE": 0}
    fields = gpio.layout(STATUS)["channels"][0]["fields"]
    assert {k: (v["lsb"], v["bits"]) for k, v in fields.items()} == {
        "ok": (0, 1), "code": (1, 3), "count": (8, 8)}


IDENT = {"gpio": "id", "parameters": {"stamp": "the build's checksum"},
         "channels": [{"direction": "in", "fields": [
             {"name": "stamp", "bits": 32, "value": "stamp", "cell": "k"}]}]}


def test_a_constant_field_reads_a_constant_cell_not_a_port():
    block = gpio.words(IDENT)
    assert block["nodes"]["k"] == {
        "block": gpio.CONST, "build": {"WIDTH": 32, "VALUE": "stamp"}}
    # nothing to wire from outside but the register port
    assert [p["name"] for p in block["inputs"]] == ["regs", "aclk", "aresetn"]
    assert ["k.q", "id.gpio_i"] in block["connections"]
    assert gpio.layout(IDENT)["channels"][0]["fields"]["stamp"] == {
        "lsb": 0, "bits": 32, "constant": True}


def test_the_digest_covers_positions_and_nothing_else():
    base = {"ctrl": gpio.layout(CONTROL), "stat": gpio.layout(STATUS)}
    d = gpio.digest(base)
    import copy
    worded = copy.deepcopy(base)
    worded["stat"]["channels"][0]["fields"]["ok"]["description"] = "reworded"
    worded["stat"]["channels"][0]["fields"]["ok"]["value"] = 7
    assert gpio.digest(worded) == d
    moved = copy.deepcopy(base)
    moved["stat"]["channels"][0]["fields"]["count"]["lsb"] = 9
    assert gpio.digest(moved) != d
    defaulted = copy.deepcopy(base)
    defaulted["ctrl"]["channels"][0]["fields"]["mode"]["default"] = 1
    assert gpio.digest(defaulted) != d


def test_fields_that_overflow_their_word_are_refused():
    bad = {"gpio": "g", "channels": [{"direction": "in", "width": 4,
                                      "fields": [{"name": "x", "bits": 5}]}]}
    with pytest.raises(ValueError, match="take 5 bits of 4"):
        gpio.words(bad)


def _bench_layout():
    path = Path(__file__).parents[1] / "revela/boards/pynq_z2/bench/layout.py"
    spec = importlib.util.spec_from_file_location("bench_layout", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Layout


class FakeMMIO:
    def __init__(self):
        self.words = {0: 0, 8: 0}

    def read(self, offset):
        return self.words[offset]

    def write(self, offset, value):
        self.words[offset] = value


def test_the_bench_reads_and_writes_fields_by_name(tmp_path):
    import json
    (tmp_path / "gpio_layout.json").write_text(json.dumps(
        {"ctrl": gpio.layout(CONTROL), "stat": gpio.layout(STATUS)}))
    Layout = _bench_layout()
    fields = Layout.beside(tmp_path / "rx.bit")
    m = FakeMMIO()
    fields.write(m, "ctrl", "mode", 3)
    fields.write(m, "ctrl", "reset", 1)
    assert m.words[0] == (3 << 3) | 1
    fields.write(m, "ctrl", "mode", 0)
    assert m.words[0] == 1                      # the rest of the word kept
    m.words[0] = (0x5A << 8) | (5 << 1) | 1
    assert (fields.read(m, "stat", "ok"), fields.read(m, "stat", "code"),
            fields.read(m, "stat", "count")) == (1, 5, 0x5A)
    with pytest.raises(KeyError, match="no field 'nope'"):
        fields.read(m, "stat", "nope")


def test_the_bench_refuses_a_layout_the_fabric_does_not_read_back(tmp_path):
    import json
    layout = {"id": gpio.layout(IDENT)}
    layout["id"]["channels"][0]["fields"]["stamp"]["value"] = 0xC0FFEE
    (tmp_path / "gpio_layout.json").write_text(json.dumps(layout))
    fields = _bench_layout().beside(tmp_path / "rx.bit")
    m = FakeMMIO()
    m.words[0] = 0xC0FFEE
    fields.verify(lambda name: m)
    m.words[0] = 0xBADF00D
    with pytest.raises(RuntimeError, match="reads 0x0BADF00D, the file says "
                                           "0x00C0FFEE"):
        fields.verify(lambda name: m)
    (tmp_path / "gpio_layout.json").write_text(json.dumps(
        {"stat": gpio.layout(STATUS)}))
    with pytest.raises(RuntimeError, match="states no constant"):
        _bench_layout().beside(tmp_path / "rx.bit").verify(lambda name: m)
