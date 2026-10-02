# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Load a block hierarchy from JSON: composites of composites, down to leaves.

Everything is a BLOCK, and every folder is a LIBRARY of blocks. A block is
named the way Python names an entry point, from the top of the import path:

    revela.blocks.ccm:ccm                  a leaf: an @ispblock in ccm.py
    np2hw.frame_history:frame_history      a leaf: a structural generator
    revela.boards.zynq7.ip:axi_gpio        a primitive: a JSON block, no nodes
    revela.pipelines.bayer_isp:bayer_isp      a composite: a JSON block with nodes

A module alone names the block called after its file: ``revela.blocks.ccm``
is ``revela.blocks.ccm:ccm``, the common case spelt once; the full form
names any block in a file that holds several.

A JSON file always has one shape, ``{"blocks": {name: {...}}}``
(blocks.schema.json). A composite has ports, nodes -- each an instance of a
block, by reference -- the connections between them, and SETTINGS for what
it contains, written on the node they set:

    "isp": {"block": "revela.pipelines.bayer_isp:bayer_isp",
            "build":  {"bit_depth": 12},          what changes the hardware
            "values": {"ccm.m": [[...], ...]}}    what the hardware holds

Keys are paths relative to the node. A setting written further out wins over
one written further in, so the design -- the block you build -- has the last
word over the libraries it uses:

    leaf declaration  <  composite  <  enclosing composite  <  ...  <  design

A number or an array is a literal. A string is ALWAYS an expression, in Python
syntax, over the parameters in scope of the composite it is written in: its
own parameters, and anything inside its nodes (``isp.bit_depth``). It is
parsed with ``ast`` and evaluated by one walker that knows integer arithmetic,
shifts, comparisons, ``a if c else b``, ``min``, ``max`` and ``abs`` -- never
by ``eval``. What an expression becomes depends on what it references:

    nothing, or build parameters only   an integer, at load
    one register, alone                 an ALIAS: two paths, one register
    registers in arithmetic             refused, until np2hw can trace
                                        arithmetic between two parameters

What load() returns is the tree FLATTENED: every leaf instance at its full
path (``isp.ccm``), with its build settings and register values resolved,
and every connection joined across composite boundaries into an edge between
two leaves. The hierarchy survives as paths, which are the same paths in the
register map, the host API and a control surface.

Libraries outside revela are found the same way as revela's own: by the
import path, never by a file path, so a reference means the same thing on
every machine. ``acme.cams.imx999:outdoor`` is ``acme/cams/imx999.json``
wherever ``acme`` is importable -- installed, on PYTHONPATH, or named for one
run with a tool's ``--library DIR``. The folders need no ``__init__.py``.

Refusals name the path. A setting of something that does not exist is a typo;
a value outside its register is a picture that would be wrong; a cycle has no
answer.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "blocks.schema.json"


class CompositionError(ValueError):
    """A hierarchy that cannot be built, and where."""


# -- references ------------------------------------------------------------- #

@dataclass(frozen=True)
class Found:
    """What a reference names."""
    ref: str
    kind: str          # "composite" | "primitive" | "profile" | "block" | "generator"
    obj: object        # the JSON dict, the Block, or the callable
    folder: Path       # where the file lives: a primitive's files are beside it


LIBRARY_HELP = ("a directory to find libraries in, for this run: a "
                "reference a.b:name then also looks for DIR/a/b.json. "
                "Repeatable; the first given is searched first")


def add_libraries(directories) -> None:
    """Put library directories on the import path, first given first.

    A reference never holds a file path -- it means the same thing on
    every machine -- so a library outside any installed package is made
    findable the one way everything is found: by the import path."""
    import sys
    for directory in reversed(list(directories or ())):
        path = Path(directory).expanduser().resolve()
        if not path.is_dir():
            raise CompositionError(f"--library {directory}: not a directory")
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    importlib.invalidate_caches()


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@lru_cache(maxsize=None)
def _library_file(path: Path) -> dict:
    import jsonschema
    data = json.loads(path.read_text())
    try:
        jsonschema.validate(instance=data, schema=schema())
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path) or "(top)"
        raise CompositionError(f"{path}: at {where}: {error.message}") from None
    return data


def canonical(ref: str) -> str:
    """A reference in its full form. ``package.module`` alone names the
    block called after its file -- ``revela.pipelines.bayer_isp`` is
    ``revela.pipelines.bayer_isp:bayer_isp`` -- which is the common case, one
    block to a file, spelt once. Everything that compares references
    compares this form."""
    module, sep, name = ref.partition(":")
    if not sep:
        name = module.rpartition(".")[2]
    return f"{module}:{name}"


def block_name(ref: str) -> str:
    """The block's own name, after the colon of the full form."""
    return canonical(ref).rpartition(":")[2]


def find(ref: str) -> Found:
    """Resolve ``package.module:block`` -- or ``package.module``, the block
    named after its file -- through importlib, so a checkout and an
    installed wheel behave the same. A module that is both a Python file
    and a JSON file is refused: one name, one thing. The Found carries the
    full form."""
    ref = canonical(ref)
    module, sep, name = ref.partition(":")
    package, dot, leaf = module.rpartition(".")
    if not (dot and name):
        raise CompositionError(f"{ref!r} is not a block reference "
                               "(package.module[:block])")
    try:
        pkg = importlib.import_module(package)
    except ImportError as error:
        raise CompositionError(f"{ref}: cannot import {package}: {error}") \
            from None
    # A folder of data files need not be a Python package with an
    # __init__.py: a namespace package has a path and no file.
    folder = Path(next(iter(pkg.__path__))).resolve()
    source = folder / f"{leaf}.json"
    python = importlib.util.find_spec(module) is not None
    if source.is_file() and python:
        raise CompositionError(
            f"{ref}: {module} is both a Python module and {source.name}; "
            "a library holds one thing under one name")
    if source.is_file():
        library = _library_file(source)
        blocks = library.get("blocks", {})
        profiles = library.get("profiles", {})
        if name in blocks and name in profiles:
            raise CompositionError(f"{ref}: {source} names both a block and "
                                   f"a profile {name!r}; a name means one "
                                   "thing in a file")
        if name in profiles:
            return Found(ref, "profile", profiles[name], folder)
        if name not in blocks:
            raise CompositionError(f"{ref}: {source} defines "
                                   f"{sorted(blocks) + sorted(profiles)}, "
                                   f"not {name!r}")
        block = blocks[name]
        kind = "composite" if "nodes" in block else "primitive"
        return Found(ref, kind, block, folder)
    if not python:
        raise CompositionError(f"{ref}: no module {module} and no "
                               f"{source.name} in {folder}")
    obj = getattr(importlib.import_module(module), name, None)
    if obj is None:
        raise CompositionError(f"{ref}: {module} has no {name!r}")
    from revela.blocks import Block
    if isinstance(obj, Block):
        return Found(ref, "block", obj, folder)
    if callable(obj):
        return Found(ref, "generator", obj, folder)
    raise CompositionError(f"{ref}: {module}.{name} is neither a block nor "
                           "a generator")


# -- expressions ------------------------------------------------------------ #

_BINARY = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.FloorDiv: lambda a, b: a // b,
           ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a ** b,
           ast.LShift: lambda a, b: a << b, ast.RShift: lambda a, b: a >> b,
           ast.BitAnd: lambda a, b: a & b, ast.BitOr: lambda a, b: a | b,
           ast.BitXor: lambda a, b: a ^ b}
_UNARY = {ast.USub: lambda a: -a, ast.UAdd: lambda a: +a,
          ast.Invert: lambda a: ~a, ast.Not: lambda a: int(not a)}
_COMPARE = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
            ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
            ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}
_CALLS = {"min": min, "max": max, "abs": abs}


def parse(text: str, where: str) -> ast.expr:
    """Parse an expression and check every node is one the walker knows. A
    string that is not an expression is refused here, at load, with its
    path -- not later, where it would look like something else."""
    try:
        tree = ast.parse(text, mode="eval").body
    except SyntaxError as error:
        raise CompositionError(f"{where}: {text!r} is not an expression: "
                               f"{error.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute, ast.Load, ast.IfExp,
                             ast.BoolOp, ast.And, ast.Or)):
            continue
        if isinstance(node, ast.Constant):
            # Integers, and strings for the vendor settings that are words
            # ('/ps7/FCLK_CLK0'); a register value is still checked against
            # its register, which no string fits.
            if type(node.value) not in (int, str):
                raise CompositionError(
                    f"{where}: {text!r}: {node.value!r} -- a constant is an "
                    "integer, or a string for a vendor setting")
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            continue
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            continue
        if isinstance(node, ast.Compare) and all(type(op) in _COMPARE
                                                 for op in node.ops):
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _CALLS and not node.keywords):
            continue
        if type(node) in _BINARY or type(node) in _UNARY or \
                type(node) in _COMPARE:
            continue                     # operator tokens inside the above
        what = ("true division; use // (exact integers only)"
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                else type(node).__name__)
        raise CompositionError(f"{where}: {text!r}: {what} is not allowed "
                               "in a parameter expression")
    return tree


def _dotted(node: ast.expr) -> str | None:
    """``a.b.c`` as a string, if the node is a parameter path."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id not in _CALLS:
        return ".".join([node.id, *reversed(parts)])
    return None


def references(tree: ast.expr) -> list[str]:
    """Every parameter path an expression reads, outermost chains only."""
    found, inner = [], set()
    for node in ast.walk(tree):
        if id(node) in inner:
            continue
        path = _dotted(node)
        if path is not None:
            found.append(path)
            inner.update(id(n) for n in ast.walk(node))
    return found


def evaluate(tree: ast.expr, lookup) -> int:
    """The one walker. ``lookup(path)`` binds each parameter path; with ints
    bound the result is an int, exactly as the model would compute it."""
    def walk(node):
        path = _dotted(node)
        if path is not None:
            return lookup(path)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            for op, right in zip(node.ops, node.comparators):
                right = walk(right)
                if not _COMPARE[type(op)](left, right):
                    return 0
                left = right
            return 1
        if isinstance(node, ast.BoolOp):
            values = [walk(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return int(all(values))
            return int(any(values))
        if isinstance(node, ast.IfExp):
            return walk(node.body) if walk(node.test) else walk(node.orelse)
        if isinstance(node, ast.Call):
            return _CALLS[node.func.id](*(walk(a) for a in node.args))
        raise AssertionError(f"unchecked node {type(node).__name__}")
    return walk(tree)


# -- the loaded hierarchy --------------------------------------------------- #

@dataclass
class Instance:
    """One leaf, at its full path, with everything set on it resolved."""
    path: str
    ref: str
    kind: str                    # "block" | "generator" | "primitive"
    leaf: object                 # the (configured) Block, the callable, the dict
    folder: Path
    build: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    domain: str | None = None
    description: str = ""
    # A vendor primitive's properties, verbatim: its own names, its own
    # value syntax (No_buffer, 74.250), never expressions.
    config: dict = field(default_factory=dict)


@dataclass
class Design:
    """A hierarchy, flattened: leaves by path, edges between leaves."""
    ref: str
    description: str
    parameters: dict             # the top block's own build parameters
    inputs: tuple
    outputs: tuple
    instances: dict              # path -> Instance, in declaration order
    composites: dict             # path -> ref, "" for the top block
    edges: list                  # (source, sink): "path.port" or a top port
    aliases: dict                # "path.register" -> "path.register" it follows
    constraints: list = field(default_factory=list)
    # every constraints file the design is built with, from the blocks that
    # own them, in declaration order: [Path]
    pins: dict = field(default_factory=dict)
    # external pin name -> its declaration ({"dir": ...} or {"interface":
    # true}), gathered from every composite, which is where each is owned
    streams: dict = field(default_factory=dict)
    # top input -> the facts of the stream entering it (bits, max_width,
    # max_height, context), from every input port it passes through on its
    # way in: stated once where it enters, inherited by what wraps it.


@dataclass
class _Node:
    path: str
    found: Found
    parent: str | None
    entry: dict                  # the node entry that instantiated it
    children: dict = field(default_factory=dict)   # local name -> path

    @property
    def kind(self):
        return self.found.kind


@dataclass
class _Setting:
    kind: str                    # "build" | "values"
    target: str                  # absolute path of the parameter
    raw: object                  # literal or expression text
    scope: str                   # path of the composite it is written in
    depth: int                   # of that composite: smaller wins
    where: str                   # for messages


def _join(*parts: str) -> str:
    return ".".join(p for p in parts if p)


def tree(ref: str) -> dict:
    """The STRUCTURE under a block: every instance path, composites and
    leaves alike, to what it is -- with no parameter evaluated, so a
    reusable composite can be walked without a design to set it."""
    top = find(ref)
    loader = _Loader()
    loader.expand("", top, None, {}, [])
    return {path: node.found for path, node in loader.nodes.items()}


def load(ref: str, build: dict | None = None) -> Design:
    """Load the block ``ref`` and everything beneath it.

    ``build`` sets the top block's own parameters, as an enclosing composite
    would; a declared default applies where it does not.
    """
    loader = _Loader()
    return loader.run(ref, build or {})


class _Loader:
    def __init__(self):
        self.nodes: dict[str, _Node] = {}
        self.settings: dict[tuple[str, str], _Setting] = {}

    # -- expanding the tree ------------------------------------------------- #

    def expand(self, path: str, found: Found, parent, entry, stack):
        if found.ref in stack:
            raise CompositionError(
                f"{path or found.ref}: {found.ref} contains itself "
                f"({' -> '.join([*stack, found.ref])})")
        node = _Node(path, found, parent, entry)
        self.nodes[path] = node
        if found.kind != "composite":
            return
        depth = 0 if not path else path.count(".") + 1
        clash = sorted(set(found.obj.get("parameters", {}))
                       & set(found.obj["nodes"]))
        if clash:
            raise CompositionError(
                f"{found.ref}: {clash} name both a parameter and a node; a "
                "path means one thing")
        for name, child in found.obj["nodes"].items():
            child_path = _join(path, name)
            node.children[name] = child_path
            where = f"{found.ref}: node {name}"
            try:
                child_found = find(child["block"])
            except CompositionError as error:
                raise CompositionError(f"{where}: {error}") from None
            if child_found.kind == "profile":
                raise CompositionError(f"{where}: {child['block']} is a "
                                       "profile, not a block")
            if child_found.kind == "generator" and getattr(
                    child_found.obj, "composite", False):
                child_found = _generated(child_found, child, where)
            self.expand(child_path, child_found, path, child,
                        [*stack, found.ref])
            for kind in ("build", "values"):
                for key, raw in child.get(kind, {}).items():
                    self.add_setting(_Setting(
                        kind, _join(child_path, key), raw, path, depth,
                        f"{where}: {kind} {key}"))

    def add_setting(self, setting: _Setting):
        key = (setting.kind, setting.target)
        held = self.settings.get(key)
        if held is None or setting.depth < held.depth:
            self.settings[key] = setting
        elif setting.depth == held.depth:
            raise CompositionError(f"{setting.where}: sets {setting.target} "
                                   f"a second time ({held.where})")

    # -- naming parameters -------------------------------------------------- #

    def split(self, path: str, where: str) -> tuple[_Node, str]:
        """An absolute parameter path, as (the instance it belongs to, the
        rest). Walks instance names as far as they go."""
        node = self.nodes[""]
        parts = path.split(".") if path else []
        i = 0
        while i < len(parts) and parts[i] in node.children:
            node = self.nodes[node.children[parts[i]]]
            i += 1
        rest = ".".join(parts[i:])
        if not rest:
            raise CompositionError(f"{where}: {path} is an instance, not a "
                                   "parameter")
        return node, rest

    def classify(self, path: str, where: str) -> str:
        """"build" or "register", or a refusal naming what exists."""
        node, rest = self.split(path, where)
        kind = node.kind
        if kind == "primitive" and _vendor_ip(node.found.obj):
            # A vendor's IP owns its parameter list, and its tool refuses a
            # name it does not have; restating the list here would be a
            # second copy of the vendor's.
            return "build"
        if kind in ("composite", "primitive"):
            declared = node.found.obj.get("parameters", {})
            if rest in declared:
                return "build"
            children = sorted(node.children)
            raise CompositionError(
                f"{where}: {path}: {node.found.ref} has no parameter "
                f"{rest!r} (parameters {sorted(declared)}"
                + (f", nodes {children})" if children else ")"))
        if kind == "generator":
            accepted = _generator_parameters(node.found.obj)
            if rest in accepted:
                return "build"
            raise CompositionError(
                f"{where}: {path}: {node.found.ref} takes "
                f"{sorted(accepted)}, not {rest!r}")
        block = node.found.obj
        registers = {p.name for p in block.params.params}
        head, _, attr = rest.partition(".")
        if head in registers and not attr:
            return "register"
        if head in registers and attr and "." not in attr:
            return "build"               # a register attribute: gain.frac
        raise CompositionError(
            f"{where}: {path}: block {block.name} has no register {head!r} "
            f"(registers {sorted(registers)})")

    def resolve_name(self, scope: str, name: str) -> str:
        """A path as written in the composite at ``scope``, made absolute.
        An expression reaches only its own composite's parameters and what is
        inside its nodes -- never its surroundings."""
        return _join(scope, name)

    # -- build parameters --------------------------------------------------- #

    def build_value(self, path: str, where: str, active: tuple):
        if path in self.build_cache:
            return self.build_cache[path]
        if path in active:
            cycle = " -> ".join([*active[active.index(path):], path])
            raise CompositionError(f"{where}: a cycle: {cycle}")
        setting = self.settings.get(("build", path))
        if setting is None:
            node, rest = self.split(path, where)
            declared = (node.found.obj.get("parameters", {})
                        if node.kind in ("composite", "primitive") else {})
            if rest in declared and "default" in declared[rest]:
                # A default may be derived: a string is an expression, in
                # the scope of the block that declares it.
                value = self.evaluate_build(
                    declared[rest]["default"], node.path,
                    f"{node.found.ref}: default of {rest}", (*active, path))
            elif node.kind in ("composite", "primitive"):
                raise CompositionError(
                    f"{where}: {path} is never set, and {node.found.ref} "
                    "declares no default for it")
            else:
                raise CompositionError(f"{where}: {path} is never set")
        else:
            value = self.evaluate_build(setting.raw, setting.scope,
                                        setting.where, (*active, path))
        self.build_cache[path] = value
        return value

    def evaluate_build(self, raw, scope: str, where: str, active=()):
        """A literal, or an expression over build parameters in ``scope``,
        as an integer."""
        if not isinstance(raw, str):
            return raw
        tree = parse(raw, where)

        def lookup(name):
            target = self.resolve_name(scope, name)
            if self.classify(target, where) != "build":
                raise CompositionError(
                    f"{where}: {raw!r} reads {name}, a register; a build "
                    "parameter changes the hardware and cannot follow a "
                    "value software writes")
            return self.build_value(target, where, active)
        return evaluate(tree, lookup)

    # -- the streams entering ----------------------------------------------- #

    STREAM_FACTS = ("bits", "max_width", "max_height", "context")

    def streams(self, forward: dict) -> dict:
        """For each top input, the facts of the stream entering it,
        gathered from every composite input port it passes through. Each
        fact is stated once -- where the stream enters -- and a composite
        that wraps it inherits it; two ports stating it differently are
        refused, and so is a fact stated on an output, where the stream is
        what the blocks made it."""
        for path, node in self.nodes.items():
            if node.kind != "composite":
                continue
            for port in node.found.obj.get("outputs", []):
                stated = [f for f in self.STREAM_FACTS if f in port]
                if stated:
                    raise CompositionError(
                        f"{node.found.ref}: output {port['name']} states "
                        f"{stated}; an output carries what the blocks "
                        "produce, which is traced, not declared")

        def declared(pin):
            node = self.nodes[pin[0]]
            port = next(p for p in node.found.obj.get("inputs", [])
                        if p["name"] == pin[1])
            where = f"{node.found.ref}: input {pin[1]}"
            facts = {}
            for fact in self.STREAM_FACTS:
                if fact in port:
                    facts[fact] = (list(port[fact]) if fact == "context"
                                   else self.evaluate_build(
                                       port[fact], pin[0], f"{where}: {fact}"))
            return facts, where

        result = {}
        top = self.nodes[""]
        for port in top.found.obj.get("inputs", []):
            facts, source = {}, {}
            pending, seen = [("", port["name"])], set()
            while pending:
                pin = pending.pop()
                if pin in seen:
                    continue
                seen.add(pin)
                here, where = declared(pin)
                for fact, value in here.items():
                    if fact in facts and facts[fact] != value:
                        raise CompositionError(
                            f"{where}: {fact} = {value}, but "
                            f"{source[fact]} states {facts[fact]} for the "
                            f"same stream (entering {port['name']})")
                    facts[fact], source[fact] = value, where
                for nxt in forward.get(pin, []):
                    if nxt[0] and self.nodes[nxt[0]].kind == "composite":
                        pending.append(nxt)
            result[port["name"]] = facts
        return result

    # -- the run ------------------------------------------------------------ #

    def run(self, ref: str, top_build: dict) -> Design:
        top = find(ref)
        if top.kind != "composite":
            raise CompositionError(f"{ref} is a {top.kind}, not a composite; "
                                   "a design is a composite of blocks")
        self.expand("", top, None, {}, [])
        for key, raw in top_build.items():
            self.add_setting(_Setting("build", key, raw, "", -1,
                                      f"load({ref!r}): build {key}"))
        self.build_cache: dict[str, object] = {}

        # Every build setting names something that exists, and is BUILD.
        for (kind, target), setting in self.settings.items():
            what = self.classify(target, setting.where)
            if kind == "build" and what != "build":
                raise CompositionError(
                    f"{setting.where}: {target} is a register; set it under "
                    "values, not build")
            if kind == "values" and what != "register":
                raise CompositionError(
                    f"{setting.where}: {target} is a build parameter; set "
                    "it under build, not values")

        # What this build has: a node whose `when` is false is not built,
        # and neither is anything inside it.
        self.inactive: set[str] = set()
        for path, node in self.nodes.items():
            when = node.entry.get("when")
            if when is None or any(path.startswith(p + ".")
                                   for p in self.inactive):
                continue
            where = f"{self.nodes[node.parent].found.ref}: node " \
                    f"{path.rpartition('.')[2]}: when"
            if not self.evaluate_build(when, node.parent, where):
                self.inactive.add(path)
        self.inactive = {p for p in self.nodes
                         if any(p == q or p.startswith(q + ".")
                                for q in self.inactive)}

        # Every declared parameter of every composite, evaluated -- an unset
        # one is an error even where nothing reads it yet: it is part of
        # the interface, and the interface is incomplete.
        for path, node in self.nodes.items():
            if path in self.inactive:
                continue
            if node.kind == "composite":
                for name in node.found.obj.get("parameters", {}):
                    self.build_value(_join(path, name),
                                     f"{node.found.ref}: parameter {name}", ())

        instances, aliases = {}, {}
        for path, node in self.nodes.items():
            if node.kind == "composite" or path in self.inactive:
                continue
            instances[path] = self.instance(path, node, aliases)

        design = Design(
            ref=top.ref, description=top.obj.get("description", ""),
            parameters={name: self.build_cache[name]
                        for name in top.obj.get("parameters", {})},
            inputs=tuple(p["name"] for p in top.obj.get("inputs", [])),
            outputs=tuple(p["name"] for p in top.obj.get("outputs", [])),
            instances=instances,
            composites={p: n.found.ref for p, n in self.nodes.items()
                        if n.kind == "composite" and p not in self.inactive},
            edges=self.edges(),
            aliases=aliases,
            pins=self.pins(),
            constraints=self.constraints(),
        )
        design.streams = self.streams(self.forward)
        return design

    def instance(self, path: str, node: _Node, aliases: dict) -> Instance:
        build = {k[1][len(path) + 1:]: self.build_value(k[1], s.where, ())
                 for k, s in self.settings.items()
                 if k[0] == "build" and k[1].startswith(path + ".")
                 and self.split(k[1], s.where)[0] is node}
        leaf = node.found.obj
        overrides: dict = {}
        if node.kind == "block":
            for key, value in build.items():
                register, attr = key.split(".")
                overrides.setdefault(register, {})[attr] = value
            try:
                leaf = leaf.configure(overrides or None)
            except (ValueError, KeyError, TypeError) as error:
                raise CompositionError(f"{path}: {error}") from None
        values = {}
        for (kind, target), setting in self.settings.items():
            if kind != "values" or self.split(target, setting.where)[0] \
                    is not node:
                continue
            register = target[len(path) + 1:]
            value = self.register_value(setting, target, aliases)
            if value is not None:
                _check_register(leaf, register, value, setting.where)
                values[register] = value
        if node.kind == "block" and values:
            # A design's values are the register file's RESET values: part
            # of the configured variant, so the map, the RTL's reset and the
            # model's defaults all read them from one place.
            leaf = node.found.obj.configure(overrides or None, resets=values)
        config = dict(node.entry.get("config", {}))
        both = sorted(set(config) & set(build))
        if both:
            raise CompositionError(f"{path}: {both} set under both build and "
                                   "config; a property has one value")
        if config and not (node.kind == "primitive"
                           and _vendor_ip(node.found.obj)):
            raise CompositionError(f"{path}: config is a vendor IP's "
                                   "properties, and this is not vendor IP")
        return Instance(path=path, ref=node.found.ref, kind=node.kind,
                        leaf=leaf, folder=node.found.folder, build=build,
                        values=values, domain=node.entry.get("domain"),
                        description=node.entry.get("description", ""),
                        config=config)

    def register_value(self, setting: _Setting, target: str, aliases: dict):
        """A literal, a constant from build parameters, or an alias. None
        for an alias: the register's value is its source's."""
        if not isinstance(setting.raw, str):
            return setting.raw
        tree = parse(setting.raw, setting.where)
        names = references(tree)
        kinds = {name: self.classify(self.resolve_name(setting.scope, name),
                                     setting.where) for name in names}
        registers = [n for n, k in kinds.items() if k == "register"]
        if not registers:
            return evaluate(tree, lambda name: self.build_value(
                self.resolve_name(setting.scope, name), setting.where, ()))
        if len(names) == 1 and _dotted(tree) is not None:
            source = self.resolve_name(setting.scope, names[0])
            if source == target:
                raise CompositionError(f"{setting.where}: {target} follows "
                                       "itself")
            aliases[target] = source
            return None
        raise CompositionError(
            f"{setting.where}: {setting.raw!r} computes with registers "
            f"({', '.join(registers)}). A register may FOLLOW one other "
            "register (an alias); arithmetic between registers is refused "
            "until np2hw can trace it")

    # -- connections -------------------------------------------------------- #

    def ports(self, node: _Node):
        """(inputs, outputs) a node declares, or None where it cannot know
        them before it is generated."""
        if node.kind == "block":
            return set(node.found.obj.ports.inputs), \
                set(node.found.obj.ports.outputs)
        if node.kind in ("composite", "primitive"):
            obj = node.found.obj
            if node.kind == "primitive" and not ("inputs" in obj
                                                 or "outputs" in obj):
                return None
            return ({p["name"] for p in obj.get("inputs", [])},
                    {p["name"] for p in obj.get("outputs", [])})
        return None

    def edges(self) -> list[tuple[str, str]]:
        edges, self.forward = self._edges()
        return edges

    def constraints(self) -> list:
        """Every constraints file of every built block, composite or
        primitive, resolved beside the file that names it; an entry may be
        {"file", "when"}, `when` read in that block's own scope."""
        out = []
        for path, node in self.nodes.items():
            if path in self.inactive or not isinstance(node.found.obj, dict):
                continue
            for entry in node.found.obj.get("constraints", []):
                if isinstance(entry, dict):
                    where = f"{node.found.ref}: constraints {entry['file']}"
                    scope = path if node.kind == "composite" else node.parent
                    if "when" in entry and not self.evaluate_build(
                            entry["when"], scope, where):
                        continue
                    entry = entry["file"]
                f = (node.found.folder / entry).resolve()
                if f not in out:
                    out.append(f)
        return out

    def pins(self) -> dict:
        """Every external pin of the design, from whichever composite owns
        it. Pins are the FPGA's ports, so their names are global: two
        composites declaring one name is refused."""
        found, owner = {}, {}
        for path, node in self.nodes.items():
            if node.kind != "composite" or path in self.inactive:
                continue
            for pin in node.found.obj.get("pins", []):
                name = pin["name"]
                if name in found:
                    raise CompositionError(
                        f"{node.found.ref}: pin {name} is also "
                        f"{owner[name]}'s; the FPGA has one port by a name")
                found[name] = {k: v for k, v in pin.items() if k != "name"}
                owner[name] = node.found.ref
        return found

    def _edges(self):
        # Every connection, as an edge between PINS: (instance path, port).
        # A composite's boundary port is one pin seen from both sides -- its
        # parent's "child.port" and its own "port" -- so joining edges
        # through composite pins leaves edges between leaves.
        forward: dict[tuple, list] = {}
        driven: dict[tuple, str] = {}
        for path, node in self.nodes.items():
            if node.kind != "composite" or path in self.inactive:
                continue
            for entry in node.found.obj.get("connections", []):
                if isinstance(entry, dict):
                    source, sink = entry["from"], entry["to"]
                    when = entry.get("when")
                else:
                    (source, sink), when = entry, None
                where = f"{node.found.ref}: connection {source} -> {sink}"
                if when is not None and not self.evaluate_build(
                        when, path, f"{where}: when"):
                    continue
                a = self.pin(node, source, "source", where)
                b = self.pin(node, sink, "sink", where)
                for end, endpoint in ((a, source), (b, sink)):
                    if end[0] in self.inactive:
                        raise CompositionError(
                            f"{where}: {endpoint} is not built here (its "
                            "node's `when` is false); guard the connection "
                            "with the same `when`")
                if b in driven:
                    raise CompositionError(f"{where}: {sink} is already "
                                           f"driven ({driven[b]})")
                driven[b] = where
                forward.setdefault(a, []).append(b)

        def is_pin(pin):
            return pin[1] in _pin_names(self.nodes[pin[0]])

        def is_leaf(pin):
            return pin[0] == "" or self.nodes[pin[0]].kind != "composite" \
                or is_pin(pin)

        def text(pin):
            if is_pin(pin):
                return f"@{pin[1]}"
            return pin[1] if pin[0] == "" else f"{pin[0]}.{pin[1]}"

        edges = []

        def follow(start, pin, seen):
            for nxt in forward.get(pin, []):
                if nxt in seen:
                    raise CompositionError(f"{text(start)}: a loop through "
                                           "composite ports")
                if is_leaf(nxt):
                    edges.append((text(start), text(nxt)))
                else:
                    follow(start, nxt, seen | {nxt})

        for pin in list(forward):
            if is_leaf(pin):
                follow(pin, pin, {pin})
        # An open wire at a composite boundary: an output nothing inside
        # drives, or an input something inside reads and nothing outside
        # drives. Either would leave a leaf input silently undriven.
        for path, node in self.nodes.items():
            if node.kind != "composite" or path in self.inactive:
                continue
            for port in node.found.obj.get("outputs", []):
                if not self.port_built(node, port):
                    continue
                if (path, port["name"]) not in driven:
                    raise CompositionError(
                        f"{node.found.ref}: output {port['name']} is "
                        "driven by nothing inside it")
            for port in node.found.obj.get("inputs", []):
                pin = (path, port["name"])
                if path and pin in forward and pin not in driven:
                    raise CompositionError(
                        f"{path}: input {port['name']} of "
                        f"{node.found.ref} is used inside it and driven by "
                        "nothing")
        return edges, forward

    def port_built(self, node: _Node, port: dict) -> bool:
        """A port with a `when` exists only where it is true, read in its
        composite's own scope -- the consumer-select field of a control
        word, say, where there is a consumer to select."""
        if "when" not in port:
            return True
        return bool(self.evaluate_build(
            port["when"], node.path,
            f"{node.found.ref}: port {port['name']}: when"))

    def pin(self, node: _Node, endpoint: str, role: str, where: str):
        """An endpoint written in composite ``node``, as a pin. A bare name
        is the composite's own port; ``child.port`` is a node's."""
        inputs, outputs = self.ports(node)
        if "." not in endpoint and endpoint in _pin_names(node):
            return (node.path, endpoint)      # an external pin: either way
        if "." not in endpoint:
            # The composite's own boundary: from inside, its input is a
            # source and its output a sink.
            own = inputs if role == "source" else outputs
            if endpoint not in own:
                raise CompositionError(
                    f"{where}: {node.found.ref} has no "
                    f"{'input' if role == 'source' else 'output'} "
                    f"{endpoint!r} (it has {sorted(own)})")
            return (node.path, endpoint)
        child, port = endpoint.split(".")
        if child not in node.children:
            raise CompositionError(f"{where}: no node {child!r} (nodes "
                                   f"{sorted(node.children)})")
        target = self.nodes[node.children[child]]
        declared = self.ports(target)
        if declared is not None:
            side = declared[1] if role == "source" else declared[0]
            if port not in side:
                raise CompositionError(
                    f"{where}: {child} ({target.found.ref}) has no "
                    f"{'output' if role == 'source' else 'input'} {port!r} "
                    f"(it has {sorted(side)})")
        return (target.path, port)


def _generated(found: Found, entry: dict, where: str) -> Found:
    """A composite a Python function writes from its node's `config` --
    a GPIO block from its field layout, say: one declaration, and every
    cell, setting and port it implies derived from it. The result is an
    ordinary composite, checked against the schema like one written by
    hand."""
    import jsonschema
    config = entry.get("config", {})
    try:
        block = found.obj(config)
    except (KeyError, ValueError, TypeError) as error:
        raise CompositionError(f"{where}: {found.ref}: {error}") from None
    sub = {"$ref": "#/$defs/composite", "$defs": schema()["$defs"]}
    try:
        jsonschema.validate(instance=block, schema=sub)
    except jsonschema.ValidationError as error:
        at = "/".join(str(p) for p in error.absolute_path) or "(top)"
        raise CompositionError(f"{where}: {found.ref} wrote a composite "
                               f"that is not one: at {at}: {error.message}") \
            from None
    return Found(found.ref, "composite", block, found.folder)


def _shared_subsystems(design: Design) -> dict:
    """Composite instances to emit as ONE module instantiated per instance.

    A composite instantiated more than once, built identically and made of
    blocks alone, is the same sub-graph twice -- a stereo pair's eyes --
    and is emitted once, as the flat format's subsystem was. Addresses are
    allocated per instance either way, so the register map is the same; only
    the emitted structure changes. A composite used once stays spelled out,
    so a design's RTL does not change for wrapping a chain in a block."""
    from revela.compose import Subsystem
    by_ref: dict = {}
    for path, ref in design.composites.items():
        if path:
            by_ref.setdefault(ref, []).append(path)
    shared = {}
    for ref, paths in by_ref.items():
        if len(paths) < 2:
            continue
        obj = find(ref).obj
        connections = obj.get("connections", [])
        if any(isinstance(c, dict) and "when" in c for c in connections) or \
                any("when" in n for n in obj["nodes"].values()):
            continue
        templates = []
        for path in paths:
            inner = [(i.path[len(path) + 1:], i.leaf)
                     for i in design.instances.values()
                     if i.path.startswith(path + ".")]
            if any("." in name for name, _ in inner) or \
                    len(inner) != len(obj["nodes"]):
                break                       # holds composites, not blocks alone
            templates.append(tuple(inner))
        else:
            if all(t == templates[0] for t in templates):
                edges = tuple((c["from"], c["to"]) if isinstance(c, dict)
                              else tuple(c) for c in connections)
                subsystem = Subsystem(
                    name=block_name(ref),
                    inputs=tuple(p["name"] for p in obj.get("inputs", [])),
                    outputs=tuple(p["name"] for p in obj.get("outputs", [])),
                    nodes=templates[0], edges=edges)
                shared.update({path: subsystem for path in paths})
    return shared


def _vendor_ip(obj) -> bool:
    """A leaf whose settings pass straight to its tool -- vendor IP, or a
    procedure -- which checks them: restating its list here would be a
    second copy of the tool's."""
    v = obj.get("vivado", {}) if isinstance(obj, dict) else {}
    return "ip" in v or "tcl" in v


def _pin_names(node) -> set:
    obj = node.found.obj
    if node.kind != "composite":
        return set()
    return {p["name"] for p in obj.get("pins", [])}


def _generator_parameters(fn) -> set[str]:
    return {name for name, p in inspect.signature(fn).parameters.items()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}


def _check_register(block, register: str, value, where: str) -> None:
    """A value must be the register's shape and fit its width."""
    param = next(p for p in block.params.params if p.name == register)
    low, high = param.limits

    def shape_of(v):
        if isinstance(v, list):
            inner = {shape_of(x) for x in v}
            if len(inner) != 1:
                raise CompositionError(f"{where}: {register}: a ragged array")
            return (len(v), *inner.pop())
        return ()

    got = shape_of(value)
    if got != tuple(param.shape):
        raise CompositionError(f"{where}: {register} has shape "
                               f"{tuple(param.shape)}, given {got}")

    def check(v):
        if isinstance(v, list):
            for x in v:
                check(x)
        elif not low <= v <= high:
            raise CompositionError(
                f"{where}: {v} does not fit {register} ({param.q_format}, "
                f"{low}..{high})")
    check(value)


# -- into today's build ----------------------------------------------------- #

def to_pipeline(design: Design, name: str | None = None):
    """Build a :class:`revela.compose.Pipeline` from a loaded design.

    Every leaf must be an @ispblock: this is the ISP, the part generated and
    proved from its models. The stream facts come from the design's inputs,
    where the stream enters. Today's Pipeline builds every block around ONE
    geometry and traces depth from ONE input depth, so every input must
    state the same facts; carrying geometry block by block, through a block
    that changes it, belongs to the compiler that traces shapes.
    """
    from revela.compose import Pipeline
    from revela.stream import StreamSpec

    name = name or block_name(design.ref)
    others = [f"{i.path} ({i.kind})" for i in design.instances.values()
              if i.kind != "block"]
    if others:
        raise CompositionError(
            f"{design.ref}: {', '.join(others)} -- a pipeline is made of "
            "@ispblocks; everything else is built around it")
    if design.aliases:
        raise CompositionError(
            f"{design.ref}: {sorted(design.aliases)} follow other registers; "
            "two blocks sharing one register is not built yet")
    if not design.inputs:
        raise CompositionError(f"{design.ref}: no inputs, so no stream")

    facts = None
    for port in design.inputs:
        stream = design.streams.get(port, {})
        missing = [f for f in ("bits", "max_width", "max_height")
                   if f not in stream]
        if missing:
            raise CompositionError(
                f"{design.ref}: the stream entering {port} states no "
                f"{', '.join(missing)}; say it on the input port where the "
                "stream enters")
        here = {f: stream[f] for f in ("bits", "max_width", "max_height")}
        here["context"] = tuple(stream.get("context", ()))
        if facts is not None and here != facts[1]:
            raise CompositionError(
                f"{design.ref}: the streams entering {facts[0]} and {port} "
                f"differ ({facts[1]} vs {here}); one pipeline is built "
                "around one stream shape today")
        facts = (port, here)
    stream = facts[1]

    pipeline = Pipeline(
        name=name,
        spec=StreamSpec(bit_depth=int(stream["bits"])),
        width=int(stream["max_width"]),
        height=int(stream["max_height"]),
        inputs=design.inputs,
        outputs=design.outputs,
        context_from_stream=stream["context"],
    )
    shared = _shared_subsystems(design)
    added = set()
    for instance in design.instances.values():
        owner = instance.path.rpartition(".")[0]
        if owner in shared:
            if owner not in added:
                added.add(owner)
                pipeline.add_subsystem(owner, shared[owner])
            continue
        pipeline.add(instance.path, instance.leaf)
    for source, sink in design.edges:
        pipeline.connect(source, sink)
    pipeline.composites = [{"path": path, "block": ref}
                           for path, ref in design.composites.items()]
    pipeline.validate()
    return pipeline
