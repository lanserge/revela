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


def chain(*blocks: str, prefix: str | None = None,
          source: str = "in", sink: str = "out") -> tuple[list, list]:
    """Nodes and connections for a linear run of blocks, with sinks TAPPED.

    A block with no outputs -- statistics, say -- does not advance the datapath.
    It taps whatever the last real block produced and the stream carries on past
    it, which is the distinction a list of blocks cannot express and the reason
    the description is a netlist.
    """
    from revela.blocks import resolve

    nodes, connections = [], []
    current = source
    for name in blocks:
        instance = f"{prefix}.{name}" if prefix else name
        ports = resolve(name).ports
        nodes.append({"instance": instance, "block": name})
        connections.append({"from": current, "to": f"{instance}.{ports.inputs[0]}"})
        if ports.outputs:
            current = f"{instance}.{ports.outputs[0]}"
    connections.append({"from": current, "to": sink})
    return nodes, connections


def describe(name: str, *chains: tuple[list, list], bit_depth: int = 12,
             width: int = 64, height: int = 32,
             inputs: tuple[str, ...] = ("in",),
             outputs: tuple[str, ...] = ("out",), **extra) -> dict:
    """An inline pipeline description, for tests that need a specific shape.

    Goes through the same schema and the same builder as a description on disk.
    There is one way to describe a pipeline and this is not a second one -- it
    just spells the JSON in Python rather than reading it from a file.
    """
    nodes, connections = [], []
    for node_list, connection_list in chains:
        nodes += node_list
        connections += connection_list
    return {
        "schema_version": 1,
        "name": name,
        "stream": {"bit_depth": bit_depth},
        "geometry": {"width": width, "height": height},
        "inputs": [{"name": n} for n in inputs],
        "outputs": [{"name": n} for n in outputs],
        "nodes": nodes,
        "connections": connections,
        **extra,
    }


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
    """Build one design with Verilator and run its cocotb testbench.

    Args:
        verilog: the complete generated source.
        toplevel: module to elaborate.
        test_module: importable module in ``tests/`` holding the cocotb tests.
        case: stimulus and configuration, handed to the testbench as JSON. The
            testbench recomputes the expected values from the NumPy model
            itself, so the model stays the single reference rather than being
            copied into the test.

    Raises:
        AssertionError: if any cocotb test failed.
    """
    from cocotb_tools.runner import get_runner, get_results

    source = tmp_path / f"{toplevel}.v"
    source.write_text(verilog)

    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))

    build_dir = tmp_path / "sim_build"
    runner = get_runner("verilator")
    runner.build(
        sources=[source],
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        always=True,
        # np2hw's cores compare unsigned counters against constants and widen
        # accumulators intentionally; those warnings are expected and are not
        # allowed to fail the build. Genuine errors still do.
        build_args=["-Wno-fatal", "--timing"],
        timescale=("1ns", "1ps"),
    )
    results = runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        test_dir=str(TESTS_DIR),
        build_dir=build_dir,
        timescale=("1ns", "1ps"),
        extra_env={"REVELA_CASE": str(case_path)},
    )
    total, failed = get_results(results)
    assert failed == 0, (
        f"{failed} of {total} cocotb test(s) failed for {toplevel}; "
        f"see {results} and the log above")
