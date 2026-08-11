# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Run a pipeline -- or any sub-chain of it -- as an image function.

A design already carries everything this needs: the netlist says which block
feeds which, and every block carries its own NumPy model. Composing those
gives each design a second life as a library of image functions: the whole
pipeline is one function, and any run of consecutive blocks is another --
inject a frame at one port, extract at another.

Injection is governed by the same contract as NumPy itself: the model's
arithmetic states its own requirements, and the person injecting mid-chain
is trusted to know what the port eats. An array the math cannot digest
fails inside the model; an array it CAN digest is accepted, meaningful or
not. That is a deliberate trade -- just math, no meaning tags -- and it is
the same one every NumPy user already lives with.

Register values layer exactly as they do on hardware (reset, then sensor,
then profile -- see :mod:`revela.profiles`), with one CLI layer on top:
``--set instance.register=value``, checked against the declared range like
every other layer. ``explain`` reports where each value came from, because
"where did this number come from" is the first question a strange picture
raises.

The RTL twin runs the same sub-chain through generated Verilog under
Verilator, fed and compared word-for-word against the model. Verilator and
not Icarus: the composed first-light pipeline simulated a full 1296x972
frame in under a second compiled, where the event-driven run had not
reached time zero in an hour. Bit-exactness over full frames is this
project's definition of correct, so the twin is built to be cheap enough
to run on every tuning iteration.
"""
from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np
from np2hw.netlist import Endpoint

# Where compiled Verilator twins live, keyed by content hash: same design,
# same geometry, same values -> the build is already there. A tuning loop
# pays the C++ compile once per STRUCTURE, not once per parameter change
# (values travel in a side file the harness reads at run time).
CACHE_ROOT = Path.home() / ".cache" / "revela" / "verilator"


# --------------------------------------------------------------------------- #
# Chain selection
# --------------------------------------------------------------------------- #

def pixel_chain(pipeline, start: str | None = None, stop: str | None = None):
    """The consecutive pixel-carrying stages between two ports.

    Args:
        pipeline: a composed :class:`revela.compose.Pipeline`.
        start: ``instance.port`` naming an INPUT port to inject at, or None
            for the pipeline's own input.
        stop: ``instance.port`` naming an OUTPUT port to extract at, or None
            for the pipeline's own output.

    Statistics taps are not part of any chain: they observe the stream and
    never transform it, which is exactly what the netlist's tap notion
    already records -- so the walk asks the netlist rather than keeping its
    own list of which blocks merely watch.
    """
    if start is None:
        if len(pipeline.inputs) != 1:
            raise ValueError(
                f"pipeline {pipeline.name!r} has inputs {list(pipeline.inputs)}; "
                "name the injection point with --from")
        first = _sole_consumer(pipeline, Endpoint(None, pipeline.inputs[0]))
    else:
        instance, port = _split_port(start)
        stage = pipeline.stage(instance)
        if port not in stage.block.ports.inputs:
            raise ValueError(
                f"--from {start!r}: {instance!r} has no input port {port!r}; "
                f"its inputs are {list(stage.block.ports.inputs)}")
        first = stage

    stop_instance = stop_port = None
    if stop is not None:
        stop_instance, stop_port = _split_port(stop)
        stage = pipeline.stage(stop_instance)
        if stop_port not in stage.block.ports.outputs:
            raise ValueError(
                f"--to {stop!r}: {stop_instance!r} has no output port "
                f"{stop_port!r}; its outputs are "
                f"{list(stage.block.ports.outputs)}")

    chain = [first]
    while True:
        stage = chain[-1]
        if stage.path == stop_instance:
            return chain
        outputs = stage.block.ports.outputs
        if not outputs:
            raise ValueError(
                f"the chain dead-ends at {stage.path!r}, which is a sink"
                + (f", before reaching --to {stop!r}" if stop else
                   " -- it never reaches the pipeline output"))
        if len(outputs) > 1:
            raise ValueError(
                f"{stage.path!r} has outputs {list(outputs)}; a forking chain "
                "has no single downstream -- name the leg with --to")
        consumers = pipeline.consumers(Endpoint(stage.path, outputs[0]))
        if stop is None and any(c.node is None for c in consumers):
            return chain
        onward = [c for c in consumers
                  if c.node is not None and not pipeline._is_tap(c)]
        if not onward:
            raise ValueError(
                f"nothing downstream of {stage.path}.{outputs[0]} carries "
                f"pixels" + (f"; --to {stop!r} is not on this chain"
                             if stop else ""))
        if len(onward) > 1:
            raise ValueError(
                f"{stage.path}.{outputs[0]} forks to "
                f"{[str(c) for c in onward]}; name the leg with --to")
        chain.append(pipeline.stage(onward[0].node))


def _sole_consumer(pipeline, source: Endpoint):
    consumers = [c for c in pipeline.consumers(source)
                 if c.node is not None and not pipeline._is_tap(c)]
    if len(consumers) != 1:
        raise ValueError(
            f"{source} feeds {[str(c) for c in consumers]}; "
            "name the injection point with --from")
    return pipeline.stage(consumers[0].node)


def _split_port(text: str) -> tuple[str, str]:
    instance, dot, port = text.rpartition(".")
    if not dot:
        raise ValueError(
            f"{text!r} does not name a port; ports are written "
            "instance.port, e.g. ha_green.out")
    return instance, port


# --------------------------------------------------------------------------- #
# Values: reset -> sensor -> profile -> --set, with provenance
# --------------------------------------------------------------------------- #

FROM_CLI = "cli"


def resolve_values(pipeline, design: dict, profile: dict | None = None,
                   sets: dict[str, dict[str, int]] | None = None):
    """Every register value for the run, and where each one came from.

    With a profile, :func:`revela.profiles.resolve` does the layering it
    already owns. Without one, the same first two layers apply -- reset
    values, then what the DESIGN's own sensor implies -- so ``revela run``
    on a bare design still black-levels at the sensor's pedestal instead
    of silently running everything at reset. The CLI's ``--set`` is one
    more layer with the same range checks, never a bypass.
    """
    from revela import profiles, sensors

    if profile is not None:
        settings = profiles.resolve(profile, pipeline)
        values = {path: dict(registers)
                  for path, registers in settings.values.items()}
        origin = {path: dict(registers)
                  for path, registers in settings.origin.items()}
    else:
        values = {stage.path: dict(stage.paramset.defaults())
                  for stage in pipeline.stages}
        origin = {path: {name: profiles.FROM_DEFAULT for name in registers}
                  for path, registers in values.items()}
        if "sensor" in design:
            sensor = sensors.load(design["sensor"]["name"])
            mode = design["sensor"].get("mode")
            for stage in pipeline.stages:
                if stage.block.sensor_hook is None:
                    continue
                for name, value in stage.block.sensor_hook(sensor, mode).items():
                    _assign(stage, values, origin, name, value,
                            profiles.FROM_SENSOR)

    for path, registers in (sets or {}).items():
        stage = pipeline.stage(path)
        for name, value in registers.items():
            _assign(stage, values, origin, name, value, FROM_CLI)
    return values, origin


def _assign(stage, values, origin, name, value, source) -> None:
    if name not in values[stage.path]:
        raise KeyError(
            f"block {stage.path!r} has no register {name!r}; "
            f"it declares {sorted(values[stage.path])}")
    param = stage.paramset.param(name)
    low, high = param.limits
    if not low <= int(value) <= high:
        raise ValueError(
            f"{stage.path}.{name} = {value} is outside [{low}, {high}] for a "
            f"{param.bits}-bit {'signed' if param.signed else 'unsigned'} "
            f"register ({param.q_format}). {param.description}")
    values[stage.path][name] = int(value)
    origin[stage.path][name] = source


def parse_sets(entries) -> dict[str, dict[str, int]]:
    """``instance.register=value`` strings into ``{instance: {register: int}}``."""
    out: dict[str, dict[str, int]] = {}
    for entry in entries or ():
        name, eq, raw = entry.partition("=")
        if not eq:
            raise ValueError(f"--set {entry!r}: expected instance.register=value")
        instance, register = _split_port(name)
        try:
            value = int(raw, 0)
        except ValueError:
            raise ValueError(
                f"--set {entry!r}: {raw!r} is not an integer; registers hold "
                "raw fixed-point values -- quantise on the host first") from None
        out.setdefault(instance, {})[register] = value
    return out


def explain(chain, values, origin) -> str:
    """Provenance for every register the run reads, one line each.

    Context first: ``pipe`` is what every block sees, and it is where a
    sensor-derived surprise (the wrong CFA order, say) will show up.
    """
    paths = (["pipe"] if "pipe" in values else []) + [s.path for s in chain]
    lines = []
    for path in paths:
        for name in sorted(values[path]):
            lines.append(f"{path}.{name} = {values[path][name]}"
                         f"  ({origin[path][name]})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The model run
# --------------------------------------------------------------------------- #

def run_model(chain, frame: np.ndarray, values, context: dict,
              bit_depth: int) -> np.ndarray:
    """Run the chain's NumPy models in order. Every hop is the block's own
    :meth:`revela.blocks.Block.run`, so all its declaration-driven checks
    apply here too."""
    for stage in chain:
        consumed = {name: context[name]
                    for name in stage.block.params.consumes if name in context}
        frame = stage.block.run(frame, values.get(stage.path),
                                bit_depth=bit_depth, **consumed)
    return frame


# --------------------------------------------------------------------------- #
# Frame files: .npy for exact integers, .png for eyeballs
# --------------------------------------------------------------------------- #

def read_frame(path: str | Path, bit_depth: int) -> np.ndarray:
    """Load a frame. ``.npy`` carries raw register-domain integers verbatim;
    ``.png`` is an 8-bit view scaled up to the datapath's ``bit_depth``."""
    path = Path(path)
    if path.suffix == ".npy":
        frame = np.load(path)
        if not np.issubdtype(frame.dtype, np.integer):
            raise ValueError(
                f"{path}: dtype {frame.dtype} -- a datapath frame holds raw "
                "integers; quantise floats on the host first")
        return frame.astype(np.int64)
    if path.suffix == ".png":
        return _png_read(path).astype(np.int64) << (bit_depth - 8)
    raise ValueError(f"{path}: expected .npy or .png")


def write_frame(path: str | Path, frame: np.ndarray, bit_depth: int) -> None:
    path = Path(path)
    if path.suffix == ".npy":
        np.save(path, frame)
        return
    if path.suffix == ".png":
        if frame.ndim == 3 and frame.shape[-1] == 2:
            raise ValueError(
                "a 2-channel frame has no PNG meaning; write .npy "
                "and feed it back with --from")
        _png_write(path, (frame >> (bit_depth - 8)).astype(np.uint8))
        return
    raise ValueError(f"{path}: expected .npy or .png")


def _png_write(path: Path, image: np.ndarray) -> None:
    if image.ndim == 2:
        image = image[..., None]
    height, width, channels = image.shape
    colour = {1: 0, 3: 2}[channels]
    raw = b"".join(b"\x00" + image[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body)))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour,
                                     0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b""))


def _png_read(path: Path) -> np.ndarray:
    """8-bit greyscale or RGB, non-interlaced. Written here rather than
    imported: revela's only array dependency is numpy, and a viewer format
    does not justify a second one."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG file")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        (length,), tag = struct.unpack(">I", data[pos:pos + 4]), data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in (0, 2) or interlace:
        raise ValueError(
            f"{path}: only 8-bit greyscale or RGB PNG is read here "
            f"(depth {depth}, colour type {colour})")
    channels = 3 if colour == 2 else 1
    stride = width * channels
    raw = zlib.decompress(idat)
    out = np.zeros((height, stride), np.uint8)
    previous = np.zeros(stride, np.int64)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = np.frombuffer(
            raw, np.uint8, stride, y * (stride + 1) + 1).astype(np.int64)
        if kind == 0:
            recon = line
        elif kind == 2:                                   # up
            recon = (line + previous) & 0xff
        elif kind in (1, 3, 4):                           # sub / average / paeth
            recon = np.zeros(stride, np.int64)
            for x in range(stride):
                left = recon[x - channels] if x >= channels else 0
                above = previous[x]
                corner = previous[x - channels] if x >= channels else 0
                if kind == 1:
                    predict = left
                elif kind == 3:
                    predict = (left + above) // 2
                else:
                    p = left + above - corner
                    predict = min((left, above, corner),
                                  key=lambda v: abs(p - v))
                recon[x] = (line[x] + predict) & 0xff
        else:
            raise ValueError(f"{path}: unknown PNG filter {kind}")
        out[y] = recon
        previous = recon
    image = out.reshape(height, width, channels)
    return image[..., 0] if channels == 1 else image


# --------------------------------------------------------------------------- #
# The RTL twin
# --------------------------------------------------------------------------- #

def run_rtl(chain, frame: np.ndarray, values, context: dict,
            bit_depth: int) -> np.ndarray:
    """The same sub-chain, through generated Verilog under Verilator.

    The chain is composed into a throwaway pipeline at the FRAME's geometry
    (line buffers are sized at synthesis; a twin built wide for a small test
    frame would not be the hardware the test claims to exercise), generated,
    compiled once per structure, and fed the same words the model consumed.
    """
    from revela.compose import Pipeline
    from revela.stream import StreamSpec

    height, width = frame.shape[:2]
    in_channels = 1 if frame.ndim == 2 else int(frame.shape[-1])
    twin = Pipeline("chain",
                    StreamSpec(bit_depth=bit_depth, channels=in_channels),
                    width, height,
                    inputs=("chain_in",), outputs=("chain_out",))
    for stage in chain:
        twin.add(stage.path, stage.block)
    source = "chain_in"
    for stage in chain:
        twin.connect(source, f"{stage.path}.{stage.block.ports.inputs[0]}")
        source = f"{stage.path}.{stage.block.ports.outputs[0]}"
    twin.connect(source, "chain_out")
    generated = twin.generate(control=False)
    verilog = "\n".join(text for _, text in generated.modules)

    top_text = dict(generated.modules)[generated.top]
    scraped = _scrape_ports(top_text)
    binary = _twin_binary(verilog, generated.top, scraped)
    port_values = _port_values(twin, scraped, values, context,
                               width, height, bit_depth)

    # The output word is whatever the trace packed; the port width says so.
    out_match = re.search(
        r"output\s+(?:wire|reg)?\s*(?:\[(\d+):0\])?\s*chain_out_data",
        top_text)
    out_bits = int(out_match.group(1) or 0) + 1
    out_channels = out_bits // bit_depth
    words = _pack(frame, in_channels, bit_depth)

    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        (scratch / "in.hex").write_text(
            "\n".join(f"{w:x}" for w in words) + "\n")
        (scratch / "ports.txt").write_text(
            "\n".join(f"{n} {v:x}" for n, v in port_values.items()) + "\n")
        done = subprocess.run(
            [str(binary), str(scratch / "in.hex"), str(scratch / "out.txt"),
             str(scratch / "ports.txt"), str(len(words))],
            capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(
                f"the RTL twin did not complete the frame: {done.stdout.strip()}"
                f" {done.stderr.strip()}")
        got = np.array([int(line, 16) for line in
                        (scratch / "out.txt").read_text().split()], np.uint64)
    return _unpack(got, height, width, out_channels, bit_depth)


def _pack(frame: np.ndarray, channels: int, bit_depth: int) -> np.ndarray:
    if channels == 1:
        return frame.astype(np.uint64).ravel()
    planes = [frame[..., c].astype(np.uint64) << (c * bit_depth)
              for c in range(channels)]
    return sum(planes).ravel()


def _unpack(words: np.ndarray, height: int, width: int, channels: int,
            bit_depth: int) -> np.ndarray:
    mask = (1 << bit_depth) - 1
    if channels == 1:
        return (words & mask).astype(np.int64).reshape(height, width)
    planes = [((words >> (c * bit_depth)) & mask).astype(np.int64)
              for c in range(channels)]
    return np.stack(planes, axis=-1).reshape(height, width, channels)


def _scrape_ports(top_text: str) -> list[str]:
    """The ctx_*/param_* input ports of the generated top, from its header."""
    header = re.search(r"module \w+\s*(?:#\(.*?\))?\s*\((.*?)\);",
                       top_text, re.S).group(1)
    return [port for direction, port in
            re.findall(r"(input|output)\s+(?:wire|reg)?\s*(?:signed)?\s*"
                       r"(?:\[[^\]]+\])?\s*(\w+)", header)
            if direction == "input"
            and (port.startswith("ctx_") or port.startswith("param_"))]


def _port_values(twin, scraped: list[str], values, context, width, height,
                 bit_depth) -> dict[str, int]:
    """A value for every ctx_*/param_* port of the generated top.

    Port names are derived the same way the generator derives them -- from
    the instance path and the declared leaf register names -- so this is a
    read of the declarations, not a parallel naming scheme. The refusal on
    an uncovered port is what keeps it honest.
    """
    ports = {}
    ctx = {"width": width, "height": height,
           "window_x0": 0, "window_y0": 0,
           "window_x1": width, "window_y1": height,
           "bit_depth": bit_depth,
           "bayer_phase": context.get("bayer_phase", 0)}
    for stage in twin.stages:
        if stage.path == "pipe":
            continue
        for name, value in values[stage.path].items():
            param = stage.paramset.param(name)
            ports[f"param_{stage.module_prefix}_{name}"] = (
                value & ((1 << param.bits) - 1))

    chosen = {}
    for port in scraped:
        if port.startswith("ctx_"):
            chosen[port] = int(ctx[port[4:]])
        else:
            if port not in ports:
                raise AssertionError(
                    f"generated port {port!r} has no value; the naming here "
                    "has drifted from the generator's")
            chosen[port] = ports[port]
    return chosen


def _twin_binary(verilog: str, top: str, scraped: list[str]) -> Path:
    """Compile the twin, or reuse the cached build for this exact structure."""
    assign = "\n".join(f'    top->{port} = take("{port}");'
                       for port in scraped)
    harness = _HARNESS.replace("@TOP@", top).replace("@ASSIGN@", assign)
    key = hashlib.sha256((verilog + harness).encode()).hexdigest()[:16]
    build = CACHE_ROOT / key
    binary = build / "obj_dir" / f"V{top}"
    if binary.exists():
        return binary
    build.mkdir(parents=True, exist_ok=True)
    (build / "twin.v").write_text(verilog)
    (build / "harness.cpp").write_text(harness)
    done = subprocess.run(
        ["verilator", "--cc", "--exe", "--build", "-j", "0", "-O3",
         "-Wno-fatal", "--top-module", top, "twin.v", "harness.cpp"],
        cwd=build, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(
            f"verilator refused the twin:\n{done.stderr.strip()[-2000:]}")
    return binary


# The driver mirrors the generated testbench exactly: sample ready/valid
# BEFORE the rising edge -- that is what the always @(posedge) blocks see --
# and only then advance. Ports arrive as a name/value file so one compiled
# twin serves every parameter change.
_HARNESS = r"""#include "V@TOP@.h"
#include "verilated.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <map>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc != 5) { std::fprintf(stderr, "in out ports total\n"); return 1; }
    auto* top = new V@TOP@;
    long total = std::atol(argv[4]);

    std::vector<uint64_t> mem(total);
    FILE* fi = std::fopen(argv[1], "r");
    for (long i = 0; i < total; i++)
        if (std::fscanf(fi, "%llx", (unsigned long long*)&mem[i]) != 1) return 1;
    std::fclose(fi);

    std::map<std::string, uint64_t> port;
    FILE* fp = std::fopen(argv[3], "r");
    char name[256]; unsigned long long value;
    while (std::fscanf(fp, "%255s %llx", name, &value) == 2) port[name] = value;
    std::fclose(fp);
    auto take = [&](const char* n) {
        auto it = port.find(n);
        if (it == port.end()) { std::fprintf(stderr, "no port %s\n", n); std::exit(1); }
        return it->second;
    };
@ASSIGN@

    FILE* fo = std::fopen(argv[2], "w");
    long idx = 0, got = 0;
    int sending = 0;
    top->rst = 1; top->chain_in_valid = 0;
    top->chain_in_eol = 0; top->chain_in_last = 0; top->chain_out_ready = 1;
    top->chain_in_data = mem[0]; top->chain_in_sof = 1;
    for (int i = 0; i < 4; i++) { top->clk = 0; top->eval(); top->clk = 1; top->eval(); }
    top->rst = 0; sending = 1; top->chain_in_valid = 1;

    for (long guard = 0; guard < 4 * total + 4096; guard++) {
        top->clk = 0; top->eval();
        int ready = top->chain_in_ready, valid = top->chain_out_valid;
        uint64_t data = top->chain_out_data;
        top->clk = 1; top->eval();
        if (sending && ready) {
            if (idx == total - 1) { sending = 0; top->chain_in_valid = 0; }
            else idx++;
        }
        if (valid) {
            std::fprintf(fo, "%llx\n", (unsigned long long)data);
            if (++got == total) break;
        }
        top->chain_in_data = mem[idx];
        top->chain_in_sof = (idx == 0);
    }
    std::fclose(fo);
    if (got != total)
        std::fprintf(stderr, "frame incomplete: %ld of %ld words\n", got, total);
    delete top;
    return got == total ? 0 : 2;
}
"""
