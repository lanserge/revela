# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A leaf's declared ports, expanded to the signals its module has.

A leaf written by hand -- a board's adapter, a reset synchroniser -- states
its ports in its library entry (blocks.schema.json, ``leaf_ports``): plain
ports, streams and buses, widths as expressions over its parameters. This
turns one entry into the signals it stands for, at given parameter values,
which is what a design is wired by and what the module is held to.

The stream's signals are np2hw's, read from the generator that emits them;
the buses are the two standard ones a board meets at vendor IP.
"""
from __future__ import annotations

from np2hw.stream import STREAM_SIGNALS

# (signal, driven by the master, width: "w" for the declared width, a
# number, or a function of it)
AXILITE = (
    ("awaddr", True, "w"), ("awvalid", True, 1), ("awready", False, 1),
    ("wdata", True, 32), ("wstrb", True, 4), ("wvalid", True, 1),
    ("wready", False, 1), ("bresp", False, 2), ("bvalid", False, 1),
    ("bready", True, 1), ("araddr", True, "w"), ("arvalid", True, 1),
    ("arready", False, 1), ("rdata", False, 32), ("rresp", False, 2),
    ("rvalid", False, 1), ("rready", True, 1),
)
AXIS_REQUIRED = (("tvalid", True, 1), ("tready", False, 1),
                 ("tdata", True, "w"))
AXIS_OPTIONAL = {"tkeep": lambda w: max(1, w // 8), "tuser": 1, "tlast": 1}


def expand(port: dict, side: str, width: int) -> dict[str, tuple[str, int]]:
    """``{signal: (direction, width)}`` for one declared port.

    `side` is ``"inputs"`` or ``"outputs"``, where the port is declared:
    a stream under inputs is sunk, a bus under inputs is served. `width`
    is the port's width already evaluated."""
    name = port["name"]
    sink = side == "inputs"
    if "stream" in port and "bus" in port:
        raise ValueError(f"{name}: a port is a stream or a bus, not both")
    if "stream" in port:
        out = {}
        carried = ("valid", "ready", "data", *port["stream"])
        for suffix, _logical, w, from_source, *_ in STREAM_SIGNALS:
            if suffix not in carried:
                continue
            driven_here = from_source != sink
            out[f"{name}_{suffix}"] = ("output" if driven_here else "input",
                                       width if w is None else w)
        return out
    if "bus" in port:
        if port["bus"] == "axilite":
            table = AXILITE
        else:
            table = AXIS_REQUIRED + tuple(
                (s, True, AXIS_OPTIONAL[s]) for s in port.get("signals", ()))
        out = {}
        for signal, from_master, w in table:
            if w == "w":
                w = width
            elif callable(w):
                w = w(width)
            driven_here = from_master != sink
            out[f"{name}_{signal}"] = ("output" if driven_here else "input",
                                       w)
        return out
    if port.get("inout"):
        return {name: ("inout", width)}
    return {name: ("input" if sink else "output", width)}


def buses(ports) -> dict[str, dict]:
    """The buses among a published port list, by the name a design wires
    them by: ``{name: {"side", "stream", "ports"}}``.

    A port's bus is what its generator says it is (``port["bus"]``). An
    np2hw stream is wired by its ports' prefix (``out`` for ``out_valid``
    ...), any other bus by its bundle (``s_axis``, ``vid_io``). The side
    follows its handshake: a bus whose READY this module drives is one it
    receives, listed under inputs; one whose READY it reads, or one with
    no READY whose signals all leave (a video port), under outputs."""
    groups: dict[str, list] = {}
    for port in ports:
        if "bus" in port:
            groups.setdefault(port["bus"]["bundle"], []).append(port)
    out = {}
    for bundle, members in groups.items():
        stream = "np2hw_stream" in members[0]["bus"]["vlnv"]
        name = bundle
        if stream:
            valid = next((p["name"] for p in members
                          if p["bus"]["logical"] == "VALID"), None)
            if valid is None or not valid.endswith("_valid"):
                raise ValueError(f"stream {bundle} has no <prefix>_valid")
            name = valid[:-len("_valid")]
        ready = [p for p in members
                 if p["bus"]["logical"].endswith("READY")]
        if ready:
            side = "inputs" if ready[0]["direction"] == "output" else "outputs"
        else:
            dirs = {p["direction"] for p in members}
            side = "outputs" if dirs == {"output"} else "inputs"
        out[name] = {"side": side, "stream": stream, "ports": members}
    return out


def from_result(result: dict, generator) -> dict:
    """A leaf generator's result as the leaf's declaration.

    The generator returns what an np2hw generator returns -- ``verilog``,
    ``module`` and an ``interface`` publishing every port -- and may add
    ``file`` (the Verilog's file name, ``<module>.v`` by default) and
    ``files`` (anything else it writes beside it, by name). Nothing here
    is restated: the ports are the generator's own list."""
    iface = result.get("interface") or {}
    ports = iface.get("ports")
    if not ports:
        raise ValueError("its result publishes no ports (interface['ports'])")
    module = result["module"]
    text = result["verilog"]
    files = {result.get("file", f"{module}.v"):
             text if text.endswith("\n") else text + "\n"}
    files.update(result.get("files", {}))
    buses(ports)                    # refuses a stream without its valid
    doc = (generator.__doc__ or "").strip().splitlines()
    return {
        "description": doc[0] if doc else "",
        "vivado": {"module": module},
        "ports": ports,
        # the values a width published as an expression is read at
        "module_parameters": dict(iface.get("parameters", {})),
        # the HDL the module is built from; anything else it makes (an
        # EDID image) is written beside it but is not a source
        "generated": sorted(f for f in files if f.endswith((".v", ".sv"))),
        "files": files,
    }


def wiring(obj) -> tuple[dict, set]:
    """How a leaf is wired, as an emitter needs it: ``({stream: {"bundle",
    "signals"}}, {bus names})``.

    A stream's bundle is the name its interface carries -- the prefix, or
    `<prefix>_stream` where the prefix is a reserved word (np2hw's rule,
    which every stream attribute follows) -- and its signals the suffixes
    it carries. Buses are the leaf's other interfaces, wired by name. A
    leaf stating no ports (vendor IP) names its interfaces under
    ``vivado.interfaces``, its tool's facts."""
    from np2hw.stream import bundle_name
    if not isinstance(obj, dict):
        return {}, set()
    streams, named = {}, set(obj.get("vivado", {}).get("interfaces", []))
    if "ports" in obj:
        for name, bus in buses(obj["ports"]).items():
            if bus["stream"]:
                streams[name] = {
                    "bundle": bus["ports"][0]["bus"]["bundle"],
                    "signals": [p["name"][len(name) + 1:]
                                for p in bus["ports"]]}
            else:
                named.add(name)
        return streams, named
    for side in ("inputs", "outputs"):
        for port in obj.get(side, []):
            if "stream" in port:
                streams[port["name"]] = {
                    "bundle": bundle_name(port["name"]),
                    "signals": ["valid", "ready", "data", *port["stream"]]}
            elif "bus" in port:
                named.add(port["name"])
    return streams, named
