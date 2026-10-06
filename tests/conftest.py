# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Shared test fixtures and the Verilator/cocotb harness.

The bit-exact tests (rule 3) need a simulator. They skip cleanly when Verilator
is absent so that a contributor without it can still run everything else, but CI
installs it and runs the whole suite -- "clone and reproduce" is the project's
central claim, and a test that is always skipped proves nothing.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

TESTS_DIR = Path(__file__).parent
PROJECT_ROOT = TESTS_DIR.parent


def verilator_available() -> bool:
    return shutil.which("verilator") is not None


requires_verilator = pytest.mark.skipif(
    not verilator_available(),
    reason="Verilator is not on PATH; install it to run the bit-exact model-vs-RTL tests",
)


def block_ref(name: str) -> str:
    """A block's reference, from its registered name: ``ccm`` is
    ``revela.blocks.ccm``, ``ha_green`` is
    ``revela.blocks.demosaic.hamilton_adams:ha_green``."""
    from revela.blocks import resolve
    module = resolve(name).model.__module__
    return module if module.rpartition(".")[2] == name else f"{module}:{name}"


def chain(*blocks: str, prefix: str | None = None,
          source: str = "in", sink: str = "out") -> dict:
    """A linear run of blocks, with sinks TAPPED -- to hand to describe().

    A block with no outputs -- statistics, say -- does not advance the datapath.
    It taps whatever the last real block produced and the stream carries on past
    it, which is the distinction a list of blocks cannot express and the reason
    a design is a netlist. With a ``prefix`` the run is its own composite,
    instantiated under that name, so its blocks sit at ``prefix.block``.
    """
    from revela.blocks import resolve

    nodes, connections = {}, []
    current = "in" if prefix else source
    for name in blocks:
        ports = resolve(name).ports
        nodes[name] = {"block": block_ref(name)}
        connections.append([current, f"{name}.{ports.inputs[0]}"])
        if ports.outputs:
            current = f"{name}.{ports.outputs[0]}"
    connections.append([current, "out" if prefix else sink])
    return {"prefix": prefix, "source": source, "sink": sink,
            "nodes": nodes, "connections": connections}


def describe(name: str, *chains: dict, bit_depth: int = 12,
             width: int = 64, height: int = 32,
             inputs: tuple[str, ...] = ("in",),
             outputs: tuple[str, ...] = ("out",)) -> dict:
    """An inline design: a library holding the composite ``name``, built for
    a ``bit_depth`` stream of up to ``width`` x ``height`` on every input.

    The same format and the same loader as a design on disk -- build()
    writes it to a library and loads it by reference. There is one way to
    describe a pipeline and this is not a second one: it spells the JSON in
    Python rather than reading it from a file.
    """
    stream = {"bits": bit_depth, "max_width": width, "max_height": height}
    top = {"inputs": [{"name": n, **stream} for n in inputs],
           "outputs": [{"name": n} for n in outputs],
           "nodes": {}, "connections": []}
    blocks = {name: top}
    for run in chains:
        if run["prefix"] is None:
            top["nodes"].update(run["nodes"])
            top["connections"] += run["connections"]
            continue
        prefix = run["prefix"]
        blocks[prefix] = {"inputs": [{"name": "in"}],
                          "outputs": [{"name": "out"}],
                          "nodes": run["nodes"],
                          "connections": run["connections"]}
        top["nodes"][prefix] = {"block": "{lib}:" + prefix}
        top["connections"] += [[run["source"], f"{prefix}.in"],
                               [f"{prefix}.out", run["sink"]]]
    return {"blocks": blocks}


_LIBRARIES = []


def library(description: dict) -> str:
    """Write ``description`` as a library on the import path; return the
    reference of its first block, the design. ``{lib}`` in it stands for the
    library itself, so its blocks can name each other. Each call is a new
    package, so a description changed between calls is never read stale
    from a cache."""
    import sys
    import tempfile

    if not _LIBRARIES:
        root = Path(tempfile.mkdtemp(prefix="revela-tests-"))
        sys.path.insert(0, str(root))
        _LIBRARIES.append(root)
    package = f"revela_testlib_{len(_LIBRARIES)}"
    folder = _LIBRARIES[0] / package
    folder.mkdir()
    _LIBRARIES.append(folder)
    text = json.dumps(description).replace("{lib}", f"{package}.design")
    (folder / "design.json").write_text(text)
    return f"{package}.design:{next(iter(description['blocks']))}"


def build(description: dict):
    """The pipeline ``description`` describes, through the composite loader."""
    from np2hw import pipeline as pipelines
    return pipelines.load(library(description))


@pytest.fixture
def rng() -> np.random.Generator:
    """A seeded generator, so a failure is reproducible from the test name alone."""
    return np.random.default_rng(20260808)


def raw_frame(rng: np.random.Generator, width: int, height: int,
              bit_depth: int) -> np.ndarray:
    """A raw Bayer frame with the awkward values deliberately included.

    Uniform random pixels almost never land on the boundaries where saturation
    and sign handling break, so the extremes are placed explicitly: zero, full
    scale, and the value either side of the midpoint, which is where a datapath
    that is signed by one bit too few starts reading positive numbers as
    negative.
    """
    top = (1 << bit_depth) - 1
    mid = 1 << (bit_depth - 1)
    frame = rng.integers(0, top + 1, (height, width), dtype=np.uint16)
    corners = [0, top, mid - 1, mid, 1, top - 1]
    for i, value in enumerate(corners):
        frame[(i * 2) % height, (i * 3) % width] = value
    return frame


def run_cocotb(tmp_path: Path, verilog: str, toplevel: str, test_module: str,
               case: dict) -> None:
    """Build one design with Verilator and run a cocotb testbench over it,
    through :func:`np2hw.testing.run_cocotb`.

    A case for np2hw's testbench for any block (``np2hw.testing.block``)
    names revela's block by its registered name; this gives np2hw its
    reference.
    """
    from np2hw.testing import run_cocotb as run

    if "block" in case:
        case = dict(case, block=block_ref(case["block"]))
    run(tmp_path, verilog, toplevel, test_module, case, test_dir=TESTS_DIR)
