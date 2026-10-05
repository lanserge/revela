# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Every wire of a loaded design, checked against the leaves' own ports.

A leaf's ports are stated in the hierarchy: a generated leaf publishes the
list its module was written from, a hand-written one declares it in its
library entry (and is held to its Verilog by the tests). So a design is
checked here, once, for every toolchain -- never by reading HDL back, and
never in an emitter, where a check is a property of one tool's output.

What is refused is what a block design would quietly get wrong:

  - a stream wired to a bus, or a bus to a bus of another kind;
  - a stream, or an AXI4-Stream bus, whose data widths differ at its two
    ends -- a block design connects the low bits and calls it a warning,
    so the symptom is a picture rather than an error;
  - a net whose pins are of different widths, the same failure one wire
    at a time;
  - a net two of our leaves drive.

A leaf with no port facts -- vendor IP, whose ports are its tool's, or an
ISP block, whose streams np2hw's composer checks -- is not checked here.
That a stream or a bus is wired on its own side, and that a port exists,
is the loader's, at the connection that names it.
"""
from __future__ import annotations

from revela.designs.composite import CompositionError
from revela.designs.interfaces import buses, expand


class WiringError(CompositionError):
    """A design whose wires disagree with the leaves they join."""


_KINDS = {"np2hw_stream": "stream", ":axis:": "axis", ":aximm:": "aximm",
          ":vid_io:": "vid_io"}
# a declared bus's kind, in the same words as a published one's
_DECLARED = {"axilite": "aximm", "axis": "axis", "tmds": "tmds",
             "video": "vid_io", "axi3": "aximm", "axi4": "aximm",
             "pins": "pins"}


def _kind(vlnv: str) -> str:
    for marker, kind in _KINDS.items():
        if marker in vlnv:
            return kind
    return vlnv


def facts(inst) -> dict | None:
    """``{"signals": {name: (direction, width)}, "bundles": {name: {kind,
    side, width}}}`` for one leaf, at this instance's parameters, or None
    where it states no ports."""
    obj = inst.leaf if isinstance(inst.leaf, dict) else None
    if obj is None:
        return None
    if "ports" in obj:
        from np2hw.ports import resolve
        params = obj.get("module_parameters", {})
        signals = {p["name"]: (p["direction"], resolve(p["width"], params))
                   for p in obj["ports"]}
        bundles = {}
        for name, bus in buses(obj["ports"]).items():
            kind = _kind(bus["ports"][0]["bus"]["vlnv"])
            data = (signals.get(f"{name}_data") if kind == "stream" else
                    signals.get(f"{name}_tdata") if kind == "axis" else None)
            bundles[name] = {"kind": kind, "side": bus["side"],
                             "width": data[1] if data else None}
        return {"signals": signals, "bundles": bundles}
    if not ("inputs" in obj or "outputs" in obj):
        return None
    from revela.designs.composite import evaluate, parse
    signals, bundles = {}, {}
    for side in ("inputs", "outputs"):
        for port in obj.get(side, []):
            raw = port.get("width", 1)
            if isinstance(raw, int):
                width = raw
            else:
                # a block filled by an implementation reads the block's
                # parameters, spent on the implementation, not its build
                params = {**obj.get("block_parameters", {}), **inst.build}

                def lookup(name, port=port, params=params):
                    if name not in params:
                        raise WiringError(
                            f"{inst.path}: port {port['name']}'s width reads "
                            f"{name}, which this instance never sets")
                    return params[name]
                width = evaluate(parse(raw, f"{inst.path}.{port['name']}"),
                                 lookup)
            signals.update(expand(port, side, width))
            if "stream" in port:
                bundles[port["name"]] = {"kind": "stream", "side": side,
                                         "width": width}
            elif "bus" in port:
                axis = port["bus"] == "axis"
                bundles[port["name"]] = {
                    "kind": _DECLARED[port["bus"]], "side": side,
                    "width": width if axis else None}
    return {"signals": signals, "bundles": bundles}


def check(design) -> None:
    """Refuse a design whose wires disagree with the ports they join."""
    known = {path: facts(inst) for path, inst in design.instances.items()}
    known = {p: f for p, f in known.items() if f is not None}
    problems = []

    def split(endpoint):
        if endpoint.startswith("pin:") or "." not in endpoint:
            return None, endpoint
        path, _, port = endpoint.rpartition(".")
        return path, port

    nets: list[set] = []
    member: dict = {}

    def join(a, b):
        ia, ib = member.get(a), member.get(b)
        if ia is None and ib is None:
            member[a] = member[b] = len(nets)
            nets.append({a, b})
        elif ib is None:
            nets[ia].add(b)
            member[b] = ia
        elif ia is None:
            nets[ib].add(a)
            member[a] = ib
        elif ia != ib:
            nets[ia] |= nets[ib]
            for x in nets[ib]:
                member[x] = ia
            nets[ib] = set()

    for source, sink in design.edges:
        (sp, sport), (dp, dport) = split(source), split(sink)
        fs, fd = known.get(sp), known.get(dp)
        bs = fs["bundles"].get(sport) if fs else None
        bd = fd["bundles"].get(dport) if fd else None
        if bs or bd:
            if bs and bd:
                if bs["kind"] != bd["kind"]:
                    problems.append(f"{source} ({bs['kind']}) -> {sink} "
                                    f"({bd['kind']}): not one kind of bus")
                elif (bs["width"] is not None and bd["width"] is not None
                      and bs["width"] != bd["width"]):
                    problems.append(f"{source} ({bs['width']} bits) -> "
                                    f"{sink} ({bd['width']} bits): WIDTH "
                                    "MISMATCH")
            continue
        join((sp, sport), (dp, dport))

    for net in nets:
        widths, drivers = {}, []
        for path, port in net:
            f = known.get(path)
            if f is None or port not in f["signals"]:
                continue                 # vendor IP, or a top-level pin
            direction, width = f["signals"][port]
            widths[f"{path}.{port}"] = width
            if direction == "output":
                drivers.append(f"{path}.{port}")
        if len(set(widths.values())) > 1:
            problems.append("WIDTH MISMATCH on one net: " + ", ".join(
                f"{p} ({w} bits)" for p, w in sorted(widths.items())))
        if len(drivers) > 1:
            problems.append(f"one net driven by {sorted(drivers)}")
    if problems:
        raise WiringError(f"{design.ref}: {len(problems)} problem(s):\n  "
                          + "\n  ".join(problems))
