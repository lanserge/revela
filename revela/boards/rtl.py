# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Our modules' ports, read from their RTL: widths with parameters
resolved, and the streams each module DECLARES.

One reading of the RTL, shared by whatever checks or draws the wires
between our own modules -- today the block-design emitter, which refuses a
wire whose ends disagree before Vivado starts. A block design answers a
width mismatch with a CRITICAL WARNING and a silent connection of the low
bits, so the symptom is a picture rather than an error; every width bug
this board has had lived in exactly that gap.

Vendor IP is out of scope: its ports are not in files here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from np2hw.ipxact import STREAM_RTL_VLNV, vlnv_string
from np2hw.stream import STREAM_SIGNALS

# The stream's signals, from the generator that emits them. Restating
# them here would put the protocol in two places, and this file exists to
# catch exactly that kind of disagreement between one description and
# another -- it should not be a source of one.
def module_ports(text: str, name: str, params: dict) -> dict:
    """Port name -> width, for one module, with parameters resolved.

    Width expressions are evaluated with the module's own defaults
    overridden by whatever the block design set on the cell -- because a
    port is only as wide as the instance was told to make it.
    """
    # Comments out first: a comment in a port list may hold ");" -- the
    # scanout's did -- and ended the list there, hiding every port after it.
    text = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    m = re.search(r"^module\s+" + re.escape(name) + r"\s*(#\((.*?)\))?\s*\((.*?)\);",
                  text, re.S | re.M)
    if not m:
        return {}
    values = dict(params)
    for pname, expr in re.findall(r"parameter\s+(\w+)\s*=\s*([^,\n)]+)", m.group(2) or ""):
        values.setdefault(pname, expr.strip())
    ports = {}
    for direction, width, port in re.findall(
            r"(input|output|inout)\s+(?:wire|reg)?\s*(?:signed\s*)?"
            r"(\[[^\]]+\])?\s*(\w+)", m.group(3)):
        ports[port] = (direction, width_of(width, values))
    return ports



BUNDLE = tuple(s for s, *_ in STREAM_SIGNALS)
LOGICAL = {logical: s for s, logical, *_ in STREAM_SIGNALS}
STREAM_BUS = vlnv_string(STREAM_RTL_VLNV)

# an attribute binds to the declaration that follows it
_DECL = re.compile(
    r'\(\*\s*X_INTERFACE_INFO\s*=\s*"([^"]*)"\s*\*\)\s*'
    r'(?:input|output|inout)\s+(?:wire|reg)?\s*(?:signed\s*)?'
    r'(?:\[[^\]]+\]\s*)?(\w+)')


def port_list(text: str, module: str) -> str:
    """The module's own port list, delimited exactly as `module_ports`
    delimits it -- the optional parameter list is part of the pattern,
    so a module without one cannot match a `) (` further down the file,
    and a `);` inside a comment above the module is never its end."""
    m = re.search(r"^module\s+" + re.escape(module)
                  + r"\s*(#\((.*?)\))?\s*\((.*?)\);", text, re.S | re.M)
    return m.group(3) if m else ""


def stream_groups(text: str, module: str, ports: dict) -> tuple:
    """Every stream a module DECLARES, and which way it faces.

    The bundle comes from the module's own interface attributes, so a
    port belongs to a stream because it says so -- not because its name
    matched a pattern. That distinction is what stops `vid_data`,
    `vid_de`, `vid_vsync` from looking like half a stream, and it is why
    a module that speaks the protocol without declaring it is refused
    here rather than silently skipped.

    The DIRECTION is still the block's own testimony: on a sink, data
    arrives and ready leaves; on a source, the reverse. Reading it from
    the RTL is what lets an edge joining two sources be refused -- a
    mistake no width check would ever see, because both ends would be
    the same width.
    """
    groups: dict = {}
    labels: dict = {}
    for attribute, port in _DECL.findall(port_list(text, module)):
        parts = attribute.split()
        if len(parts) != 3 or parts[0] != STREAM_BUS:
            continue                      # someone else's interface
        _bus, _label, logical = parts
        if logical not in LOGICAL or port not in ports:
            continue
        # The GROUP is the port prefix. The interface label beside it may
        # differ -- `in` and `out` are HDL reserved words and get renamed
        # for the block design's benefit -- and a design names ports,
        # so the prefix is what has to match.
        suffix = LOGICAL[logical]
        if not port.endswith("_" + suffix):
            continue
        prefix = port[:-(len(suffix) + 1)]
        groups.setdefault(prefix, {})[suffix] = ports[port]
        labels[prefix] = _label
    out = {}
    for prefix, signals in groups.items():
        if set(signals) != set(BUNDLE):
            continue                      # incomplete; reported by the caller
        data_dir = signals["data"][0]
        ready_dir = signals["ready"][0]
        if data_dir == "input" and ready_dir == "output":
            role = "sink"
        elif data_dir == "output" and ready_dir == "input":
            role = "source"
        else:
            role = "malformed"
        out[prefix] = {"role": role, "signals": signals,
                       "width": signals["data"][1],
                       # what the block design calls this interface, which
                       # is the prefix unless the prefix is a reserved word
                       "interface": labels[prefix]}
    return out, {p: sorted(sig) for p, sig in groups.items()
                 if set(sig) != set(BUNDLE)}


def undeclared_streams(text: str, module: str, ports: dict) -> set:
    """Prefixes that look like a stream but declare nothing.

    The migration guard. A module carrying `<p>_valid` and `<p>_ready`
    and saying nothing about them is the state this file used to infer
    its way through; naming it is cheaper than inferring again.
    """
    seen: dict = {}
    for name in ports:
        m = re.match(r"(.+?)_(valid|ready)$", name)
        if m:
            seen.setdefault(m.group(1), set()).add(m.group(2))
    declared = set()
    for attribute, port in _DECL.findall(port_list(text, module)):
        parts = attribute.split()
        if len(parts) == 3 and parts[0] == STREAM_BUS:
            suffix = LOGICAL.get(parts[2], "")
            if suffix and port.endswith("_" + suffix):
                declared.add(port[:-(len(suffix) + 1)])
    return {p for p, sig in seen.items()
            if sig == {"valid", "ready"} and p not in declared}


def width_of(spec: str | None, values: dict) -> int:
    """A [hi:lo] spec as a bit count, with parameters substituted."""
    if not spec:
        return 1
    inner = spec.strip()[1:-1]
    hi, _, lo = inner.partition(":")
    try:
        return int(evaluate(hi, values)) - int(evaluate(lo, values)) + 1
    except Exception:
        return -1                      # unresolvable: reported, never assumed


def evaluate(expr: str, values: dict, depth: int = 0) -> int:
    expr = expr.strip()
    if depth > 8:
        raise ValueError(f"parameter expression too deep: {expr}")
    for name, value in values.items():
        expr = re.sub(rf"\b{re.escape(name)}\b", f"({value})", expr)
    if re.search(r"[A-Za-z_]", expr):
        raise ValueError(f"unresolved parameter in {expr!r}")
    return eval(expr, {"__builtins__": {}}, {})            # digits and operators only


def module_sources(dirs) -> dict:
    """Module name -> source text, over every Verilog file in `dirs`.

    One reading of the RTL, shared by this check and by the netlist
    generator, so the two cannot come to different conclusions about the
    same module. A module defined twice is refused: which copy the block
    design would get is not a question to leave to glob order.
    """
    sources, where = {}, {}
    for d in dirs:
        for src in sorted(Path(d).glob("*.v")):
            text = src.read_text()
            for name in re.findall(r"^module\s+(\w+)", text, re.M):
                if name in sources:
                    raise SystemExit(f"module {name} is defined in both "
                                     f"{where[name]} and {src}")
                sources[name], where[name] = text, src
    return sources
