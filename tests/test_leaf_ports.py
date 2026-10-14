# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A leaf is held to its ports: a hand-written one to what it declares, a
generated one to what its generator publishes.

A leaf written by hand -- a board's adapter, a reset synchroniser -- states
its ports in its library entry, and a design is wired by that statement,
never by reading the Verilog. So the statement has to be the module: each
such leaf the board design reaches is elaborated by Verilator, at
parameter values chosen here rather than its own defaults, and every port
it has must be declared, with the direction and the width its expression
gives.
"""
import shutil
from pathlib import Path

import pytest

from np2hw.design import composite
from np2hw.design.interfaces import expand

pytestmark = pytest.mark.skipif(shutil.which("verilator") is None,
                                reason="needs verilator")

DESIGN = "revela.boards.pynq_z2.pynq_demo:pynq_demo"
PRIMITIVES = (Path(__file__).resolve().parents[1] / "revela" / "boards"
              / "common" / "sim" / "vendor_primitives.v")

# Values no module defaults to, so a width that ignores its parameter shows.
VALUES = {"SAMPLE_BITS": 13, "DATA_BITS": 27, "WIDTH": 640, "DW": 24,
          "FB_WORD": 0}


def _hand_written():
    """revela's own hand-written leaves the board reaches; np2hw's fixed
    modules are held to their descriptions by np2hw's tests."""
    leaves = {}
    for path, found in composite.tree(DESIGN).items():
        obj = found.obj
        if found.kind == "primitive" and obj.get("sources") and not all(
                s.startswith("np2hw.hdl:") for s in obj["sources"]):
            leaves[found.ref] = found
    return sorted(leaves.items())


def _declared(obj: dict, values: dict) -> dict:
    want = {}
    for side in ("inputs", "outputs"):
        for port in obj.get(side, []):
            raw = port.get("width", 1)
            width = (raw if isinstance(raw, int) else composite.evaluate(
                composite.parse(raw, port["name"]), lambda n: values[n]))
            want.update(expand(port, side, width))
    return want


LEAVES = _hand_written()


def test_the_board_reaches_hand_written_leaves():
    assert len(LEAVES) >= 8, [ref for ref, _ in LEAVES]


@pytest.mark.parametrize("ref,found", LEAVES, ids=[r for r, _ in LEAVES])
def test_a_hand_written_leaf_has_exactly_the_ports_it_declares(ref, found):
    from np2hw.ports import elaborated_ports
    obj = found.obj
    assert "inputs" in obj or "outputs" in obj, f"{ref} declares no ports"
    params = {k: v for k, v in VALUES.items()
              if k in obj.get("parameters", {})}
    module = obj["vivado"]["module"]
    sources = [composite.source_file(found.folder, f) for f in obj["sources"]]
    # the vendor primitives a module instantiates, as stand-ins: Vivado
    # has the real ones
    sources.append(PRIMITIVES)
    got = elaborated_ports(sources, module, params)
    want = _declared(obj, params)
    assert want == got, {
        "undeclared": sorted(set(got) - set(want)),
        "not in the module": sorted(set(want) - set(got)),
        "differ": {k: (want[k], got[k]) for k in set(want) & set(got)
                   if want[k] != got[k]}}


# --------------------------------------------------------------------------- #
# A generated leaf publishes its ports, and the list is its module's
# --------------------------------------------------------------------------- #

def _generated_leaves():
    design = composite.load(DESIGN)
    return sorted((path, inst) for path, inst in design.instances.items()
                  if isinstance(inst.leaf, dict) and "made_by" in inst.leaf)


GENERATED = _generated_leaves()


def test_the_board_has_generated_leaves():
    assert {p.rpartition(".")[2] for p, _ in GENERATED} >= {
        "blrx", "ddc", "scanout_regs"}


@pytest.mark.parametrize("path,inst", GENERATED,
                         ids=[p for p, _ in GENERATED])
def test_a_generated_leaf_publishes_exactly_its_ports(path, inst):
    from np2hw.ports import elaborated_ports, resolve
    obj = inst.leaf
    texts = [obj["files"][f] for f in obj["generated"]] + [
        composite.source_file(inst.folder, f) for f in obj.get("sources", ())]
    got = elaborated_ports(texts, obj["vivado"]["module"],
                           obj["module_parameters"] or None)
    want = {p["name"]: (p["direction"],
                        resolve(p["width"], obj["module_parameters"]))
            for p in obj["ports"]}
    assert want == got
