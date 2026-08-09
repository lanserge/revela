# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Pipeline descriptions: build a pipeline from JSON instead of from Python.

A description is a NETLIST: named block instances, and the connections between
their ports. It is an **input** -- written by hand today, and by a pipeline
builder later, since a graph of named ports is exactly what a GUI edits.

A netlist rather than a list, because a real ISP is not a chain. Statistics TAP
the datapath and produce no pixels; a preview path FORKS off the main one; luma
and chroma SPLIT and only luma is sharpened; HDR MERGES two exposures. Forcing
that into an ordered list puts blocks in the datapath that are only watching
it.

    from revela.designs import load
    pipeline = load("pipelines/mono/imx219/basic/pipeline.json")
    open("build/isp.v", "w").write(pipeline.generate().verilog)
    pipeline.write_register_map("build/isp.json")

Two JSON files, pointing opposite ways
---------------------------------------

    pipeline description   INPUT    structure, no addresses    (this module)
    register map           OUTPUT   addresses, generated       (revela.pipeline)

Keeping them apart is not tidiness. A description **cannot express an address**,
and the schema rejects an ``addresses`` key outright: blocks declare local
offsets, and revela assigns each block instance a base at composition time. If a
description could pin an address, then a builder GUI could produce a map that
silently disagreed with the hardware it generated -- which is the exact failure
rule 2 exists to prevent. Addresses are allocated, never declared.

The same applies to what a description takes from a sensor. It may name one, but
only two things are read from it: bit depth, which is the datapath width, and
image width, which sizes the line buffers. Bayer phase and everything else stay
runtime registers, so one bitstream serves every sensor.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    """The pipeline description schema, loaded once."""
    return json.loads(SCHEMA_PATH.read_text())


def validate(description: dict) -> None:
    """Raise if ``description`` does not satisfy the schema.

    Raises:
        jsonschema.ValidationError: naming the failing path, so an author sees
            which field is wrong rather than that the file is wrong.
    """
    import jsonschema

    jsonschema.validate(instance=description, schema=schema())


def load(path: str | Path, validate_description: bool = True):
    """Build a :class:`revela.pipeline.Pipeline` from a description file."""
    description = json.loads(Path(path).read_text())
    if validate_description:
        validate(description)
    return build(description, validate_description=False)


def build(description: dict, validate_description: bool = True):
    """Build a pipeline from an already-loaded description.

    Blocks are resolved by name through :func:`revela.blocks.registry`, so the
    description names blocks the way a person would and never imports anything.
    That is what makes the format usable by a tool that is not Python.
    """
    from revela import sensors
    from revela.blocks import resolve
    from revela.compose import Pipeline
    from revela.stream import StreamSpec

    if validate_description:
        validate(description)

    stream = description["stream"]
    geometry = description["geometry"]
    bit_depth = int(stream["bit_depth"])
    width, height = int(geometry["width"]), int(geometry["height"])

    # A named sensor supplies the two build-time parameters, and only those.
    if "sensor" in description:
        sensor = sensors.load(description["sensor"]["name"])
        build_parameters = sensors.build_parameters(
            sensor, description["sensor"].get("mode"))
        bit_depth = build_parameters["bit_depth"]
        width = build_parameters["width"]
        mode = sensors.mode(sensor, description["sensor"].get("mode"))
        height = int(mode["height"])

    spec = StreamSpec(bit_depth=bit_depth,
                      channels=int(stream.get("channels", 1)),
                      signed=bool(stream.get("signed", False)))

    regions = description.get("regions", {})
    pipeline = Pipeline(
        name=description["name"],
        spec=spec,
        width=width,
        height=height,
        config_base=int(regions.get("config_base", 0x0000)),
        stats_base=int(regions.get("stats_base", 0x8000)),
        inputs=tuple(entry["name"] for entry in description.get("inputs", [{"name": "in"}])),
        outputs=tuple(entry["name"] for entry in description.get("outputs", [{"name": "out"}])),
    )

    subsystems = {entry["name"]: _subsystem(entry)
                  for entry in description.get("subsystems", [])}

    for node in description["nodes"]:
        if "subsystem" in node:
            try:
                template = subsystems[node["subsystem"]]
            except KeyError:
                raise KeyError(
                    f"node {node['instance']!r} instantiates subsystem "
                    f"{node['subsystem']!r}, which this design does not define; "
                    f"it defines {sorted(subsystems)}") from None
            pipeline.add_subsystem(node["instance"], template)
        else:
            pipeline.add(node["instance"], resolve(node["block"]),
                         registers=node.get("registers"))

    flattened, boundary = _flatten(description, subsystems)
    for source, sink in flattened:
        pipeline.connect(source, sink)
    pipeline.boundary_edges = boundary

    pipeline.validate()
    return pipeline


def _subsystem(entry: dict):
    """Build a Subsystem from its description."""
    from revela.blocks import resolve
    from revela.compose import Subsystem

    return Subsystem(
        name=entry["name"],
        inputs=tuple(p["name"] for p in entry.get("inputs", [{"name": "in"}])),
        outputs=tuple(p["name"] for p in entry.get("outputs", [{"name": "out"}])),
        nodes=tuple((n["instance"],
                     resolve(n["block"]).configure(n.get("registers")))
                    for n in entry["nodes"]),
        edges=tuple((e["from"], e["to"]) for e in entry["connections"]),
    )


def _flatten(description: dict, subsystems: dict):
    """Resolve subsystem boundary ports away, leaving one flat edge list.

    A subsystem boundary is a name, not a component: `left.sensor` means
    "whatever the top level connected to this instance's `sensor` input". So the
    top edge into it and the subsystem edge out of it are ONE connection, and
    joining them here keeps the pipeline graph flat -- which is why address
    allocation, the register map and the host API need to know nothing about
    subsystems at all.
    """
    instances = {node["instance"]: subsystems[node["subsystem"]]
                 for node in description["nodes"] if "subsystem" in node}

    def boundary(text):
        """(instance, port) if this endpoint is a subsystem boundary, else None."""
        instance, _, port = text.rpartition(".")
        template = instances.get(instance)
        if template and (port in template.inputs or port in template.outputs):
            return instance, port
        return None

    drives, drained = {}, {}
    passthrough = []
    for edge in description["connections"]:
        into, out_of = boundary(edge["to"]), boundary(edge["from"])
        if into:
            drives[into] = edge["from"]          # top -> subsystem input
        if out_of:
            drained.setdefault(out_of, []).append(edge["to"])   # output -> top
        if not into and not out_of:
            passthrough.append((edge["from"], edge["to"]))

    boundary = [(e["from"], e["to"]) for e in description["connections"]
                if boundary(e["to"]) or boundary(e["from"])]
    edges = list(passthrough)
    for instance, template in instances.items():
        for source, sink in template.edges:
            if source in template.inputs:
                key = (instance, source)
                if key not in drives:
                    raise ValueError(
                        f"subsystem instance {instance!r} input {source!r} is not "
                        "connected")
                source = drives[key]
            else:
                source = f"{instance}.{source}"
            if sink in template.outputs:
                for target in drained.get((instance, sink), []):
                    edges.append((source, target))
                continue
            edges.append((source, f"{instance}.{sink}"))
    return edges, boundary


def describe(pipeline) -> dict:
    """Recover a description from a built pipeline.

    The inverse of :func:`build`, and deliberately lossy in one direction: the
    allocated addresses are NOT included, because a description cannot hold them.
    Round-tripping a pipeline through this and back must produce the same
    hardware, which is only true if allocation is a pure function of structure --
    so it is worth a test, and there is one.
    """
    recovered = {
        "schema_version": 1,
        "name": pipeline.name,
        "stream": {
            "bit_depth": pipeline.spec.bit_depth,
            "channels": pipeline.spec.channels,
        },
        "geometry": {"width": pipeline.width, "height": pipeline.height},
        "regions": {
            "config_base": pipeline.allocator.config_base,
            "stats_base": pipeline.allocator.stats_base,
        },
        "inputs": [{"name": name} for name in pipeline.inputs],
        "outputs": [{"name": name} for name in pipeline.outputs],
    }

    if pipeline.subsystem_instances:
        # Recover the hierarchy, not just the flattened graph: a round trip that
        # silently un-nested a design would rebuild to different RTL, and the
        # round-trip test exists precisely to catch that.
        templates = {}
        for subsystem in pipeline.subsystem_instances.values():
            templates.setdefault(subsystem.name, subsystem)
        recovered["subsystems"] = [
            {
                "name": subsystem.name,
                "inputs": [{"name": n} for n in subsystem.inputs],
                "outputs": [{"name": n} for n in subsystem.outputs],
                "nodes": [dict({"instance": inner, "block": block.name},
                               **({"registers": block.overrides}
                                  if block.overrides else {}))
                          for inner, block in subsystem.nodes],
                "connections": [{"from": s, "to": k} for s, k in subsystem.edges],
            }
            for subsystem in templates.values()
        ]
        inside = set(pipeline.subsystem_instances)
        recovered["nodes"] = (
            [{"instance": name, "subsystem": subsystem.name}
             for name, subsystem in pipeline.subsystem_instances.items()]
            + [dict({"instance": stage.path, "block": stage.paramset.block},
                    **({"registers": stage.block.overrides}
                       if stage.block.overrides else {}))
               for stage in pipeline.nodes
               if stage.path.split(".", 1)[0] not in inside])
        recovered["connections"] = [{"from": s, "to": k}
                                    for s, k in pipeline.boundary_edges]
        return recovered

    # Declaration order, not topological order: addresses are assigned as blocks
    # are added, so reordering here would rebuild to different bases.
    recovered["nodes"] = [dict({"instance": stage.path,
                                "block": stage.paramset.block},
                               **({"registers": stage.block.overrides}
                                  if stage.block.overrides else {}))
                          for stage in pipeline.nodes]
    recovered["connections"] = [{"from": str(source), "to": str(sink)}
                                for source, sink in pipeline.edges]
    return recovered
