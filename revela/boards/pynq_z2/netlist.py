# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Emit the pixel datapath's cells and wires, from the design description.

The datapath used to be drawn by hand in Tcl, and hand-drawn wiring is
where every width bug in this repo has lived -- because a block design
answers a mismatch with a CRITICAL WARNING and a silent connection of
the low bits, so the symptom is a picture rather than an error.

So the datapath is DESCRIBED (design.json) and the Tcl is generated,
with the widths checked on the way through. An edge whose two ends
disagree stops the build here, named, before Vivado starts.

What this owns: our own blocks that carry the stream, the parameters
they are built at, and the stream edges between them.

What it deliberately does NOT own: clocks, resets, vendor IP and the
AXI automation. Those stay hand-written in bd.tcl -- a declarative
model of Vivado's interconnect automation would be a worse version of
something that already works, and the clock/reset discipline is audited
after the build by checkhwh.py, which reads what was actually produced.

    python3 -m revela.boards.pynq_z2.netlist --out <build dir>

Writes <build dir>/generated/netlist.tcl, which bd.tcl sources.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The six signals an elastic stream is made of, read from np2hw rather
# than restated -- np2hw emits the protocol and now publishes it, as an
# IP-XACT bus definition rendered from the same declaration its Verilog
# emitters attach to the ports.
#
# This file used to RECOGNISE a stream instead: a regex over port names,
# calling a prefix a stream if it carried valid and ready. That was the
# honest thing to do while there was nothing to read -- and it is exactly
# the inference IP Integrator was making about AXI4-Lite before the ports
# declared themselves. A module now says which of its ports form a
# stream, and which end of it they are, so this reads the answer.
# Reading a module's ports and the streams it declares belongs to
# the shared resolver, so this file and checknets.py cannot come
# to different conclusions about the same RTL.
from revela.boards.checknets import (BUNDLE, STREAM_BUS, module_sources,
                                     stream_groups, undeclared_streams)
from revela.boards.pynq_z2 import hdl_dirs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path,
                        help="the build directory: reads its hdl/ and "
                             "generated/params.tcl, writes netlist.tcl")
    args = parser.parse_args()

    design = json.loads((HERE / "design.json").read_text())
    params_file = args.out / "generated" / "params.tcl"
    if not params_file.exists():
        return fail("params.tcl is missing -- it is generated from the "
                    "design, the board and the ISP's contract, and this "
                    "netlist is built at the widths it names")

    from revela.boards.checknets import module_ports, tcl_values

    # The bus definition the RTL's interface attributes name, written
    # where bd.tcl points its IP catalogue. Without it Vivado reads an
    # attribute naming a VLNV it cannot resolve and says so, once per
    # port, as a CRITICAL WARNING -- and this board reads its critical
    # warnings. np2hw renders it; nothing here restates the protocol.
    from np2hw.ipxact import write_definitions

    busdef = args.out / "generated" / "busdef"
    write_definitions(busdef)

    scalars = tcl_values("", params_file.read_text())
    active = {"capture": scalars.get("p_capture") == "1",
              "genlock": scalars.get("p_genlock") == "1"}

    dirs = hdl_dirs(args.out)
    sources = module_sources(dirs)

    # Resolve every instance's ports at the width it is built with, so
    # the edges below are checked against what will actually exist.
    ports, built, streams = {}, {}, {}
    for inst in design["instances"]:
        when = inst.get("when")
        if when and not active.get(when, False):
            continue
        module = inst["module"]
        if module not in sources:
            return fail(f"{inst['name']}: module {module} is in none of "
                        f"{[str(d) for d in dirs]}")
        values = {}
        for pname, var in (inst.get("params") or {}).items():
            if var not in scalars:
                return fail(f"{inst['name']}.{pname} names {var}, which "
                            "params.tcl does not set")
            values[pname] = scalars[var]
        ports[inst["name"]] = module_ports(sources[module], module, values)
        missing = undeclared_streams(sources[module], module,
                                     ports[inst["name"]])
        if missing:
            return fail(
                f"{inst['name']} ({module}): port group(s) "
                f"{sorted(missing)} carry a valid/ready handshake and "
                f"declare no interface. Add the X_INTERFACE_INFO "
                f"attributes naming {STREAM_BUS} -- a stream is stated "
                "by the module that owns the ports, not guessed here")
        streams[inst["name"]], partial = stream_groups(
            sources[module], module, ports[inst["name"]])
        for prefix, signals in partial.items():
            return fail(f"{inst['name']} ({module}): port group {prefix!r} has "
                        f"{signals} -- a stream is all six of {list(BUNDLE)} or "
                        "it is not a stream, and a partial one is how a "
                        "ready or a last goes quietly missing")
        if "domain" not in inst:
            return fail(f"{inst['name']}: no clock domain declared. Which "
                        "clock a block runs on is the fact this board has "
                        "paid for three times; it is not optional here")
        built[inst["name"]] = inst

    lines = [
        "# GENERATED by revela.boards.pynq_z2.netlist from design.json -- do not edit.",
        "#",
        "# The pixel datapath: the blocks that carry the stream, the widths",
        "# they are built at, and the wires between them. Every width here",
        "# was checked against the modules' own ports before this file was",
        "# written; a mismatch is a refusal, not a warning.",
        "",
    ]
    # No `if {$capture}` here. The guards were resolved above, against
    # the same params.tcl the build uses, so this file contains exactly
    # the cells that exist -- guarding them a second time in Tcl would be
    # a second reading of the same fact, which is the shape of every bug
    # this whole arrangement exists to prevent.
    for name, inst in built.items():
        lines.append(f"# {inst['description']}")
        lines.append(f"set {inst['var']} [create_bd_cell -type module "
                     f"-reference {inst['module']} {name}]")
        for pname, var in (inst.get("params") or {}).items():
            lines.append(f"set_property CONFIG.{pname} ${var} ${inst['var']}")
        lines.append("")

    problems = []
    for edge in design["edges"]:
        guard = edge.get("when")
        if guard and not active.get(guard, False):
            continue
        src, dst = edge["from"], edge["to"]
        if src.startswith("@"):
            choice = edge["select"]
            key = next((k for k in choice if k != "default" and active.get(k)),
                       None)
            src = choice[key] if key else choice["default"]
        sc, _, sp = src.partition("/")
        dc, _, dp = dst.partition("/")
        if sc not in ports or dc not in ports:
            problems.append(f"{src} -> {dst}: {sc if sc not in ports else dc} "
                            "is not an instance of this datapath")
            continue
        a_grp, b_grp = streams[sc].get(sp), streams[dc].get(dp)
        if a_grp is None or b_grp is None:
            missing = src if a_grp is None else dst
            problems.append(f"{src} -> {dst}: {missing} is not a stream on "
                            "that block")
            continue
        # WHICH WAY EACH END FACES, from the RTL. Two sources joined are
        # the same width and would pass every width check ever written.
        if a_grp["role"] != "source" or b_grp["role"] != "sink":
            problems.append(
                f"{src} ({a_grp['role']}) -> {dst} ({b_grp['role']}): an edge "
                "runs from a source to a sink; this one does not")
        if a_grp["width"] != b_grp["width"]:
            problems.append(
                f"{src} ({a_grp['width']} bits) -> {dst} "
                f"({b_grp['width']} bits): WIDTH MISMATCH")
        # AND WHICH CLOCK EACH END RUNS ON. A stream handed straight from
        # one domain to another is not a wire, it is a metastability
        # generator with a picture attached; it needs a crossing, and the
        # edge has to say so.
        da, db = built[sc]["domain"], built[dc]["domain"]
        if da != db and not edge.get("crossing"):
            problems.append(
                f"{src} ({da}) -> {dst} ({db}): CLOCK DOMAINS DIFFER and the "
                "edge does not declare a crossing")
        pad = ""
        lines.append(f"# {src} -> {dst}"
                     + (f"  ({edge['description']})" if edge.get("description")
                        else ""))
        # SIX WIRES, not one interface net, and that is deliberate. Both
        # ends declare the same bus definition, so `connect_bd_intf_net`
        # works and reads better -- but an interface net leaves no
        # addressable net on the signals inside it, and this board TAPS
        # five of them (isp/in_valid, in_ready, out_valid and the shim's
        # pair) into the status word that says whether the pipeline is
        # flowing. Tried 2026-09-08: the build fails at the first tap.
        # Observability beat tidiness.
        lines.append("foreach s {" + " ".join(BUNDLE) + "} {")
        lines.append(f"{pad}    connect_bd_net [get_bd_pins {src}_$s] "
                     f"[get_bd_pins {dst}_$s]")
        lines.append("}")
        lines.append("")

    if problems:
        print(f"netlist.py: {len(problems)} problem(s)", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    out_dir = args.out / "generated"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "netlist.tcl").write_text("\n".join(lines))
    edges = sum(1 for e in design["edges"]
                if not (e.get("when") and not active.get(e["when"], False)))
    print(f"generated {out_dir / 'netlist.tcl'} -- "
          f"{len(built)} blocks, "
          f"{edges} stream edges, every width checked"
          + (" (capture)" if active["capture"] else ""))
    return 0


def fail(message: str) -> int:
    print(f"netlist.py: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
