# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""An AXI GPIO block from one declaration of its fields.

Software's control bits and the fabric's status bits have one layout, and
it is stated once: here, as a node's `config`. Everything that follows from
it is derived -- the GPIO's widths and power-on value, the slices that pick
control bits out, the concatenations that pack status bits in, the pads
that fill a word to its width -- and so is the layout file the bench
scripts read fields from by name (layout()). A bit position written into a
script beside the one the fabric uses is two answers to one question; the
second drifts first, and nothing says so.

    {"gpio": "status_gpio",
     "channels": [
       {"direction": "in", "cell": "status_cat", "fields": [
          {"name": "link_locked", "bits": 1, "description": "..."},
          {"group": "hdr_cat", "fields": [...]},
          {"pad": 10, "cell": "isl_pad"}]},
       {"direction": "out", "fields": [
          {"name": "broom", "bits": 1, "default": 0, "cell": "broom"},
          {"skip": 2},
          ...]}]}

An input channel packs its items low bit first; a group is a nested
concatenation, named by its cell, and a pad a constant of zeros. An output
channel gives each field a slice, named by its cell, and `skip` leaves bits
unused; a field's `default` is its power-on value. An input field with a
`value` reads a constant cell (named by its `cell`) rather than a port: an
expression over the declaration's parameters, so a word software can read
to learn which build it is talking to. A field or cell may carry `when`, an
expression over the parameters the declaration lists in `parameters` --
which the design sets.

The composite's ports are the fields, by name: the design wires each
status field's source to it, and each control field to what it drives.
"""
from __future__ import annotations

GPIO = "revela.boards.common.gpio"
CONST, SLICE = "np2hw.hdl.glue:const", "np2hw.hdl.glue:slice"
CONCAT = "np2hw.glue:concat"


def _items_bits(items) -> int:
    total = 0
    for item in items:
        if "group" in item:
            total += _items_bits(item["fields"])
        elif "pad" in item:
            total += item["pad"]
        elif "skip" in item:
            total += item["skip"]
        else:
            total += item.get("bits", 1)
    return total


def _fields(items, lsb=0):
    """(name, lsb, bits, item) for every named field, low bit first."""
    for item in items:
        if "group" in item:
            yield from _fields(item["fields"], lsb)
            lsb += _items_bits(item["fields"])
        elif "pad" in item or "skip" in item:
            lsb += item.get("pad", item.get("skip", 0))
        else:
            yield item["name"], lsb, item.get("bits", 1), item
            lsb += item.get("bits", 1)


def words(config: dict) -> dict:
    """The composite for one GPIO block: its fields as ports, and its
    register port (`regs`, on `aclk` and `aresetn`) for a bus to reach."""
    gpio = config["gpio"]
    channels = config["channels"]
    if not 1 <= len(channels) <= 2:
        raise ValueError(f"{gpio}: a GPIO has one or two channels")
    nodes, connections, inputs, outputs = {}, [], [], []
    build = {}
    for index, ch in enumerate(channels):
        suffix = "" if index == 0 else "2"
        width = ch.get("width", _items_bits(ch["fields"]))
        if _items_bits(ch["fields"]) > width:
            raise ValueError(f"{gpio} channel {index + 1}: its fields take "
                             f"{_items_bits(ch['fields'])} bits of {width}")
        build[f"width{suffix}"] = width
        if ch["direction"] == "out":
            build[f"out{suffix}"] = 1
            default = sum(item.get("default", 0) << lsb
                          for _, lsb, _, item in _fields(ch["fields"]))
            if default:
                build[f"default{suffix}"] = default
            pin = f"gpio{suffix}_o"
            for name, lsb, bits, item in _fields(ch["fields"]):
                cell = item["cell"]
                node = {"block": SLICE,
                        "build": {"IN": width, "HI": lsb + bits - 1,
                                  "LO": lsb}}
                port = {"name": name}
                if "description" in item:
                    node["description"] = item["description"]
                    port["description"] = item["description"]
                conn_in = {"from": f"{gpio}.{pin}", "to": f"{cell}.d"}
                conn_out = {"from": f"{cell}.q", "to": name}
                if "when" in item:
                    for x in (node, port, conn_in, conn_out):
                        x["when"] = item["when"]
                nodes[cell] = node
                outputs.append(port)
                connections += [conn_in, conn_out]
        else:
            pin = f"gpio{suffix}_i"
            items = ch["fields"]
            if "cell" not in ch:
                if len(items) != 1 or "name" not in items[0]:
                    raise ValueError(f"{gpio} channel {index + 1}: more than "
                                     "one item needs a concatenation `cell`")
                connections.append([_source(items[0], nodes, inputs),
                                    f"{gpio}.{pin}"])
            else:
                _concat(ch["cell"], items, nodes, connections, inputs)
                connections.append([f"{ch['cell']}.q", f"{gpio}.{pin}"])
    if len(channels) == 2:
        build["dual"] = 1
    nodes = {gpio: {"block": GPIO, "build": build,
                    **({"description": config["description"]}
                       if "description" in config else {})},
             **nodes}
    connections += [["regs", f"{gpio}.regs"], ["aclk", f"{gpio}.aclk"],
                    ["aresetn", f"{gpio}.aresetn"]]
    inputs = [{"name": "regs", "description": "The GPIO's registers."},
              {"name": "aclk", "description": "Their clock."},
              {"name": "aresetn", "description": "Their reset, active low."},
              *inputs]
    block = {"description": config.get("description",
                                       f"{gpio}: a GPIO, field by field"),
             "nodes": nodes, "connections": connections, "inputs": inputs}
    if config.get("parameters"):
        block["parameters"] = {name: {"description": d}
                               for name, d in config["parameters"].items()}
    if outputs:
        block["outputs"] = outputs
    return block


words.composite = True


def _source(item, nodes, inputs) -> str:
    """Where an input field's bits come from: a constant cell for a field
    with a `value`, else the composite's port of its name."""
    if "value" in item:
        node = {"block": CONST,
                "build": {"WIDTH": item.get("bits", 1),
                          "VALUE": item["value"]}}
        if "description" in item:
            node["description"] = item["description"]
        nodes[item["cell"]] = node
        return f"{item['cell']}.q"
    port = {"name": item["name"]}
    if "description" in item:
        port["description"] = item["description"]
    inputs.append(port)
    return item["name"]


def _concat(cell, items, nodes, connections, inputs):
    """A concatenation of `items`, low bit first, into `cell`: each word's
    width stated, from the declaration."""
    nodes[cell] = {"block": CONCAT,
                   "build": {"widths": [_items_bits([item]) for item in items]}}
    for i, item in enumerate(items):
        into = f"{cell}.in{i}"
        if "group" in item:
            _concat(item["group"], item["fields"], nodes, connections, inputs)
            connections.append([f"{item['group']}.q", into])
        elif "pad" in item:
            nodes[item["cell"]] = {"block": CONST,
                                   "build": {"WIDTH": item["pad"], "VALUE": 0}}
            connections.append([f"{item['cell']}.q", into])
        else:
            connections.append([_source(item, nodes, inputs), into])


def layout(config: dict) -> dict:
    """The fields of one GPIO block as software reads them: channel 1 at
    offset 0, channel 2 at 8, each field by its low bit and width."""
    out = {"channels": []}
    for index, ch in enumerate(config["channels"]):
        extra = {}
        if ch["direction"] == "out":
            # the word as the fabric powers up, whatever this build has
            extra["default"] = sum(item.get("default", 0) << lsb
                                   for _, lsb, _, item in _fields(ch["fields"]))
        out["channels"].append({
            "offset": 0 if index == 0 else 8,
            "direction": ch["direction"], **extra,
            "fields": {name: {"lsb": lsb, "bits": bits,
                              **({"default": item["default"]}
                                 if "default" in item else {}),
                              **({"constant": True}
                                 if "value" in item else {}),
                              **({"description": item["description"]}
                                 if "description" in item else {})}
                       for name, lsb, bits, item in _fields(ch["fields"])}})
    return out


def digest(layouts: dict) -> int:
    """A 32-bit checksum of GPIO layouts -- what software needs to agree on
    with the fabric: every block, channel and field, its position, width
    and power-on value. Not the descriptions, which move no bit, and not a
    constant's value, which is where this checksum goes."""
    import json
    import zlib

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items()
                    if k not in ("description", "value")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    text = json.dumps(strip(layouts), sort_keys=True, separators=(",", ":"))
    return zlib.crc32(text.encode())
