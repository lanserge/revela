# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Composition, address allocation, and the machine-readable register map.

This is where blocks stop being declarations and become a pipeline. Three jobs:

  1. Compose blocks in order, chaining one stream into the next.
  2. Assign each block INSTANCE a base address. Blocks declare local offsets
     only; the same block added twice gets two different bases, which is what
     makes a stereo pipeline work and what makes a block independently testable.
  3. Emit the register map as JSON. The host API and the documentation are both
     generated from that file. Nothing anywhere hardcodes an address.

That last point is the one that decays quietly if it is not enforced, so it is
worth stating what it rules out: no constant in a host script, no ``#define`` in
a C header checked into this repo, no address in a docstring that a reader might
copy. If software needs an address it reads the JSON, and if the JSON is stale
the ID-and-version word at each block's local offset 0 catches it before the
first pixel moves.

Layout
------

    0x0000  pipe            pipeline context, fanned out to blocks as wires
    0x0100  first block instance
    0x0200  second block instance
    ...
    0x8000  statistics region -- RAM windows, double-buffered, bulk read

Every config block starts on a 256-byte boundary, so the address decoder is a
bit-slice compare (``addr[15:8] == 8'h01``) rather than a pair of magnitude
comparators. Address space is free; decode logic and readable output are not.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from revela.blocks import Generated, comment, spdx_header
from revela.blocks import pipe as pipe_block
from revela.params import (
    BLOCK_ALIGN,
    ID_VERSION_OFFSET,
    REG_WIDTH,
    AddressAllocator,
    BlockInstance,
    ParamSet,
)
from revela.stream import StreamSpec

# Bumped when the STRUCTURE of the emitted JSON changes, so a host can refuse a
# map it does not understand instead of misreading it.
#
#   2  added `control` (the bus, and what the commit and the error response mean);
#      `commit` on a register became "frame_boundary", which is what the hardware
#      does -- the copy happens as the frame in flight ends, so the NEXT frame is
#      the first to see the write.
MAP_FORMAT_VERSION = 2


# One end of a connection. np2hw's: the netlist is np2hw's structure, and a
# second Endpoint here would be the graph vocabulary described twice.
from np2hw.netlist import Endpoint, Netlist, Node  # noqa: E402


@dataclass(frozen=True)
class Stage:
    """One block instance in the pipeline, with everything needed to wire it."""

    path: str
    block: "Block"
    instance: BlockInstance

    @property
    def paramset(self) -> ParamSet:
        return self.instance.paramset

    @property
    def ports(self):
        """The block's declared stream ports."""
        return self.block.ports

    @property
    def module_prefix(self) -> str:
        """Verilog-safe prefix derived from the instance path.

        ``left.blacklevel`` becomes ``left_blacklevel``, so two instances of one
        block produce two distinctly named modules and two distinct signal
        groups.
        """
        return self.path.replace(".", "_")


@dataclass(frozen=True)
class Subsystem:
    """A reusable sub-graph, composed once and instantiated by name.

    A stereo camera is two eyes running the same chain. Describing that as a
    subsystem emits ONE module instantiated twice rather than the same graph
    copied into the top level -- which is what the netlist meant all along, and
    what `np2hw.compose()` returning its own interface makes expressible.

    Addresses are still allocated per INSTANCE: a `blacklevel` inside subsystem
    instance `left` is the instance `left.blacklevel`, with its own registers.
    So the register map is byte-identical whether a design uses subsystems or
    spells the graph out, and only the emitted structure differs. There is a test
    for that, because it is the property that makes the change safe.
    """

    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    nodes: tuple                    # ((inner_instance, Block), ...)
    edges: tuple                    # ((source, sink), ...) as raw strings

    @property
    def blocks(self) -> dict:
        return dict(self.nodes)


class Pipeline:
    """A composed ISP pipeline: a NETLIST of block instances, with addresses.

    Blocks are connected port to port, not chained in a list. A real ISP is a
    graph: statistics tap the datapath and produce no pixels, a preview path
    forks off the main one, luma and chroma split and only luma is sharpened.
    A list cannot say any of that, and forcing it into one puts blocks in the
    datapath that are only watching it.

    ``pipe`` is added automatically at base 0. It has no stream ports, so it is
    not a node in the graph -- but it is allocated through the same :meth:`add`
    as everything else, because the moment it becomes a special case every
    future change has to remember it exists.

    Args:
        name: pipeline name; becomes the top module name and the JSON ``name``.
        spec: the input pixel stream.
        width, height: geometry the generated cores are built around. Runtime
            resolution below this comes from the pipeline context registers.
        inputs, outputs: pipeline-level stream names.
        config_base, stats_base: region bases.
    """

    def __init__(self, name: str, spec: StreamSpec, width: int, height: int,
                 config_base: int = 0x0000, stats_base: int = 0x8000,
                 inputs: Sequence[str] = ("in",), outputs: Sequence[str] = ("out",)):
        self.name = name
        self.spec = spec
        self.width = width
        self.height = height
        self.inputs = tuple(inputs)
        self.outputs = tuple(outputs)
        self.allocator = AddressAllocator(config_base=config_base, stats_base=stats_base)
        self.stages: list[Stage] = []
        # The GRAPH is np2hw's: nodes, edges, and the streaming-contract checks
        # (undriven input, double driver, cycle, unbuffered fork) live with the
        # emitter whose handshake they describe. revela holds what a node MEANS.
        self.netlist = Netlist(name, external_inputs=self.inputs,
                               external_outputs=self.outputs)
        # {instance name: Subsystem}, in declaration order.
        self.subsystem_instances: dict[str, Subsystem] = {}
        # Top-level edges in BOUNDARY form (`l_in -> left.sensor`), kept so a
        # description can be recovered with its hierarchy intact. The graph
        # itself holds the flattened equivalents.
        self.boundary_edges: list[tuple[str, str]] = []
        self.add("pipe", pipe_block.pipe)

    # -- composition ---------------------------------------------------------- #

    def add(self, path: str, block, registers: dict | None = None) -> Stage:
        """Add one INSTANCE of a block at ``path``, allocating it a base address.

        Args:
            path: dotted instance path, e.g. ``"left.blacklevel"``. It is the
                instance's identity: it names the Verilog module, keys the JSON
                map, and becomes the host attribute chain ``dev.left.blacklevel``.
            block: the :class:`revela.blocks.Block` -- its model, register set,
                ports and context declarations, in one object.
            registers: build-time overrides of declared register attributes,
                ``{"gain": {"frac": 12}}`` -- the block's declaration says
                which attributes it allows. This configures a VARIANT of the
                block; identically-configured instances share one variant
                object, which is what keeps them one TYPE downstream.
        """
        from revela.blocks import Block

        if not isinstance(block, Block):
            raise TypeError(
                f"expected a Block declared with @ispblock, got "
                f"{type(block).__name__}")

        block = block.configure(registers)
        for signal in block.params.consumes:
            pipe_block.resolve(signal)      # raises with the available names

        if any(stage.path == path for stage in self.stages):
            raise ValueError(
                f"{path!r} is already in this pipeline; a second instance needs its "
                "own path (that is the point -- instances, not blocks, get addresses)")
        instance = self.allocator.allocate(path, block.params)
        stage = Stage(path=path, block=block, instance=instance)
        self.stages.append(stage)
        ports = block.ports
        if ports.inputs or ports.outputs:
            self.netlist.add(Node(path, inputs=tuple(ports.inputs),
                                  outputs=tuple(ports.outputs)))
        return stage

    def add_subsystem(self, instance: str, subsystem: Subsystem) -> list[Stage]:
        """Instantiate a subsystem, allocating every block inside it.

        The inner blocks are added at ``<instance>.<inner>``, so they are
        ORDINARY instances as far as address allocation, the register map, the
        host API and profiles are concerned. The graph stays flat -- the caller
        connects the flattened endpoints -- and the subsystem is remembered only
        so that generation can emit one module and instantiate it, instead of
        emitting the same sub-graph once per instance.

        That is what keeps the change safe: a design using subsystems and the
        same design spelled out produce a byte-identical register map, and only
        the emitted structure differs.
        """
        if instance in self.subsystem_instances:
            raise ValueError(f"subsystem instance {instance!r} is already present")
        self.subsystem_instances[instance] = subsystem
        return [self.add(f"{instance}.{inner}", block)
                for inner, block in subsystem.nodes]

    def connect(self, source: str, sink: str) -> None:
        """Connect one port to another. Endpoints are ``instance.port`` or a
        pipeline input/output name."""
        self.netlist.connect(source, sink)

    # -- lookup --------------------------------------------------------------- #

    def stage(self, path: str) -> Stage:
        for stage in self.stages:
            if stage.path == path:
                return stage
        raise KeyError(f"no block instance {path!r}; this pipeline has "
                       f"{[s.path for s in self.stages]}")

    def address_of(self, path: str, register: str) -> int:
        """Absolute address of one register of one instance."""
        return self.stage(path).instance.address_of(register)

    # -- graph ---------------------------------------------------------------- #
    #
    # All structure questions go to np2hw's netlist: the four validations
    # (undriven input, double driver, cycle, unbuffered fork) are rules about
    # np2hw's streaming handshake, and this file re-deriving them would keep a
    # copy of the emitter's law -- which it did, until the fork rule existed in
    # two places. What revela adds is only the mapping from nodes to STAGES,
    # because a node is a name and a stage is a block with registers.

    @property
    def edges(self) -> list[tuple[Endpoint, Endpoint]]:
        return self.netlist.edges

    def consumers(self, source: Endpoint) -> list[Endpoint]:
        return self.netlist.consumers(source)

    def driver(self, sink: Endpoint) -> Endpoint | None:
        return self.netlist.driver(sink)

    def validate(self) -> None:
        """Check the netlist is a well-formed, buildable graph.

        The checks are np2hw's -- each one is a real hardware failure in its
        handshake, not a tidiness rule -- so np2hw performs them.
        """
        self.netlist.validate()

    def _is_tap(self, sink: Endpoint) -> bool:
        """Whether a sink absorbs the stream without ever stalling it."""
        return self.netlist.is_tap(sink)

    def order(self) -> list[Stage]:
        """Stages in topological order, so generation wires sources before sinks."""
        return [self.stage(node.name) for node in self.netlist.order()]

    @property
    def datapath(self) -> list[Stage]:
        """Nodes that carry pixels, in topological order (``pipe`` excluded).

        Generation order. NOT the order addresses were allocated in -- see
        :attr:`nodes` for that, and note the difference matters: a description
        recovered in the wrong order rebuilds to different addresses.
        """
        return [s for s in self.order() if s.path != "pipe"]

    @property
    def nodes(self) -> list[Stage]:
        """Nodes in DECLARATION order, which is the order addresses were assigned."""
        return [s for s in self.stages if s.path != "pipe"]

    # -- register map --------------------------------------------------------- #

    def register_map(self) -> dict:
        """The whole address map as a JSON-able dict.

        This is the single source of truth that the host API and the register-map
        documentation are generated from.
        """
        blocks = []
        for stage in self.stages:
            paramset, inst = stage.paramset, stage.instance
            registers = []
            for reg in paramset.registers:
                param = reg.param
                registers.append({
                    "name": param.name,
                    "offset": reg.offset,
                    "address": inst.base + reg.offset,
                    "bits": param.bits,
                    "signed": param.signed,
                    "frac": param.frac,
                    "q_format": param.q_format,
                    "default": param.default,
                    "access": "rw",
                    "commit": "frame_boundary",
                    "description": param.description,
                })
            windows = []
            for window in paramset.stats:
                base = inst.stats_bases[window.name]
                windows.append({
                    "name": window.name,
                    "base": base,
                    "buffer_bytes": window.buffer_bytes,
                    "size_bytes": window.size_bytes,
                    "words": window.words,
                    "records": window.records,
                    "record_words": window.record_words,
                    "layout": list(window.layout),
                    "access": "ro",
                    "buffering": "double",
                    "description": window.description,
                })
            blocks.append({
                "path": stage.path,
                "block": paramset.block,
                "id": paramset.block_id,
                "version": f"{paramset.version[0]}.{paramset.version[1]}",
                "base": inst.base,
                "size": paramset.size_bytes,
                "description": paramset.description,
                "consumes": list(paramset.consumes),
                # Build-time register-attribute overrides this instance was
                # configured with; {} for the base declaration. Informative:
                # every derived fact (q_format, defaults) already reflects
                # them, this records WHY.
                "register_overrides": stage.block.overrides,
                # False means the registers below are decoded and read back but
                # reach no datapath: the block is declared and not built yet.
                # Software is told, rather than finding out from an image that
                # did not change.
                "implemented": stage.block.implemented,
                "not_implemented_reason": ("" if stage.block.implemented
                                           else stage.block.not_traceable),
                "id_version": {
                    "offset": ID_VERSION_OFFSET,
                    "address": inst.id_version_address,
                    "value": paramset.id_version_word,
                    "access": "ro",
                    "description": "Block ID in the high half, major.minor version in "
                                   "the low half. Read it before anything else: it is "
                                   "how software proves the bitstream is the one it "
                                   "was built against",
                },
                "registers": registers,
                "statistics": windows,
            })

        return {
            "map_format_version": MAP_FORMAT_VERSION,
            "name": self.name,
            "generator": "revela",
            "licence": "Apache-2.0 WITH SHL-2.1",
            "geometry": {"width": self.width, "height": self.height},
            "stream": {
                "bit_depth": self.spec.bit_depth,
                "channels": self.spec.channels,
                "data_bits": self.spec.data_bits,
            },
            "address_bits": self.allocator.address_bits(),
            "block_align": BLOCK_ALIGN,
            "control": {
                "bus": "axi4-lite",
                "data_bits": REG_WIDTH,
                "address_bits": self.allocator.address_bits(),
                "commit": "frame_boundary",
                "commit_description":
                    "A write lands in a shadow register and is copied to the live "
                    "value at the end of the frame in flight, so every frame is "
                    "processed with one coherent set of values. A write made during "
                    "frame N therefore takes effect on frame N+1",
                "error_response":
                    "A write to a read-only word, and any access to an unmapped "
                    "address, is answered SLVERR",
            },
            "regions": {
                "config": {
                    "base": self.allocator.config_base,
                    "size": self.allocator.config_span,
                    "structure": "one 32-bit CSR per word, shadowed and committed at "
                                 "the frame boundary",
                },
                "statistics": {
                    "base": self.allocator.stats_base,
                    "structure": "double-buffered RAM windows, read-only, read in bulk",
                },
            },
            "blocks": blocks,
        }

    def write_register_map(self, path: str | Path) -> Path:
        """Write the register map JSON next to the generated Verilog."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.register_map(), indent=2) + "\n")
        return path

    # -- control plane --------------------------------------------------------- #

    def address_map(self):
        """The whole register map as an ``np2hw.AddrMap``: types, then instances.

        This is the same allocation :meth:`register_map` publishes, handed to
        np2hw so it can emit the decode and render the SystemRDL. revela decides
        WHERE everything lives -- one 256-byte-aligned block per instance, an
        identity word at each base -- and np2hw decides what an AXI4-Lite slave
        and a ``.rdl`` file look like. Neither holds a copy of the other's half,
        which is the only way the JSON map and the hardware cannot drift apart.

        The hierarchy is kept: one ``RegBlock`` per block TYPE, one
        ``RegInstance`` per stage. A stereo pipeline therefore hands np2hw ONE
        ``blacklevel`` layout instantiated twice, exactly as the emitted Verilog
        has one module instantiated twice -- flattening happens inside np2hw, at
        the emitter, the one place the flat list is genuinely needed.

        Q format and fractional bits travel in ``properties``: revela's
        concepts, carried opaquely by np2hw into renderings that can hold them.
        """
        from np2hw import AddrMap, Reg, RegBlock, RegInstance

        types: dict[tuple, RegBlock] = {}
        instances = []
        for stage in self.stages:
            paramset = stage.paramset
            # Type identity includes the VARIANT: two whitebalance instances
            # with different Q formats are two types, and merging them would
            # make the map lie about one. Identically-configured instances
            # share a variant object (configure() caches), so they still
            # collapse to one type.
            type_name = paramset.block + stage.block.variant_signature
            block = types.get(type_name)
            if block is None:
                # A declared-but-unbuilt block still gets its registers decoded,
                # so the map and the hardware stay the same document. Saying so
                # in the RTL and the map is the alternative to a register that
                # quietly does nothing. A property of the TYPE, like the layout.
                unbuilt = ("" if stage.block.implemented else
                           f" NOTE: {paramset.block} is declared but not built "
                           f"yet ({stage.block.not_traceable}), so this register "
                           "reads back what was written and drives nothing.")
                regs = [Reg(
                    name="id_version", bits=32, offset=ID_VERSION_OFFSET,
                    access="ro", value=paramset.id_version_word,
                    description=f"block ID 0x{paramset.block_id:04x} in the high "
                                f"half, version {paramset.version[0]}."
                                f"{paramset.version[1]} in the low half. Read it "
                                "before anything else: it is how software proves "
                                "the bitstream is the one it was built against.")]
                for register in paramset.registers:
                    param = register.param
                    regs.append(Reg(
                        name=param.name, bits=param.bits, offset=register.offset,
                        signed=param.signed, reset=param.default,
                        description=f"({param.q_format}). "
                                    f"{param.description}.{unbuilt}",
                        properties={"q_format": param.q_format,
                                    "frac": param.frac}))
                block = types[type_name] = RegBlock(
                    name=type_name, regs=tuple(regs),
                    size=paramset.size_bytes, description=paramset.description)
            elif block.size != paramset.size_bytes or \
                    len(block.regs) != 1 + len(paramset.registers):
                raise ValueError(
                    f"two different layouts both call themselves "
                    f"{paramset.block!r}; a block TYPE has one layout")
            instances.append(RegInstance(path=stage.path, block=block,
                                         base=stage.instance.base))
        return AddrMap(name=self.name, instances=tuple(instances),
                       data_bits=REG_WIDTH,
                       description=f"{self.name} -- generated by revela; the "
                                   "emitted JSON register map is the "
                                   "authoritative companion")

    def registers(self) -> list:
        """The flat ``np2hw.Reg`` list, in address order.

        Purely :meth:`address_map` flattened -- kept as a convenience for tests
        and callers that want the flat view, and NOT a second rendering of the
        allocation: there is exactly one, and this is a projection of it.
        """
        return self.address_map().flatten()

    def write_systemrdl(self, path: str | Path) -> Path:
        """Write the register map as SystemRDL 2.0, next to the Verilog.

        SystemRDL is what an integration or verification team feeds their own
        tooling: the PeakRDL exporters turn this one file into a UVM register
        model, C headers, IP-XACT and browsable documentation. Those are tools
        the INTEGRATOR runs; revela emits the standard format and stops.
        """
        from np2hw import systemrdl

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(systemrdl(
            self.address_map(),
            header=spdx_header(
                what=f"{self.name} -- register map as SystemRDL 2.0",
                source="the same allocation as the emitted JSON map")))
        return path

    def control_bind(self, built: dict) -> dict:
        """What drives each parameter port of the composed datapath top.

        Three kinds of port, and the distinction is the whole of rule 2:
        a block's own configuration comes from its own register; pipeline
        context comes from ``pipe``'s registers, ONE copy fanned out to every
        consumer; and a context BIT is a slice of one of those.
        """
        bind = {f"ctx_{ctx.name}": f"param_pipe_{ctx.name}"
                for ctx in pipe_block.CONTEXT}
        for path, result in built.items():
            stage = self.stage(path)
            for name, _ in result.params:
                bind[f"param_{stage.module_prefix}_{name}"] = \
                    f"param_{stage.module_prefix}_{name}"
        return bind

    # -- generation ------------------------------------------------------------ #

    def generate(self, control: bool = True) -> Generated:
        """Emit every block's Verilog, and hand the netlist to np2hw to compose.

        revela emits no Verilog. It describes: which instances exist, how they
        connect, what drives their parameter ports, and what every register
        means. np2hw generates the block datapaths from the models and the top
        level from the netlist, because it is np2hw that decides what a
        generated core's ports are and what the valid/ready contract means.

        Args:
            control: wrap the datapath in the AXI4-Lite register file, which is
                what a design needs to be configurable on hardware. ``False``
                stops at the datapath top, whose parameters are flat input
                ports -- what a bit-exact testbench wants, because it drives the
                coefficients directly instead of writing them over a bus.
        """
        from np2hw import Connection, Instance, Port, StreamType, compose

        self.validate()

        modules: list[tuple[str, str]] = []
        built: dict[str, Generated] = {}
        # A block inside a subsystem is generated ONCE, under a name that does
        # not mention which instance it ended up in -- that is the whole point.
        seen: dict[str, Generated] = {}
        for stage in self.datapath:
            if not stage.block.traceable:
                continue                    # declared, not yet built (e.g. stats)
            shared = self._subsystem_of(stage.path)
            name = (f"revela_{shared.name}_{stage.path.split('.', 1)[1]}"
                    if shared else f"revela_{stage.module_prefix}")
            if name in seen:
                built[stage.path] = seen[name]
                continue
            result = stage.block.generate(self.spec, self.width, self.height,
                                          module_name=name)
            modules.extend(result.modules)
            built[stage.path] = seen[name] = result

        # The pipeline's own ports take their domain from the block at the
        # boundary, so a design cannot declare its input to be something the
        # first block does not accept.
        stream = StreamType(self.spec.data_bits, ("sof", "eol", "last"),
                            self._boundary_domain())
        top = compose(
            module_name=self.name,
            instances=self._instances(built),
            connections=self._connections(built),
            ports=self._ports(built, stream),
            header=spdx_header(
                what=f"{self.name} -- composed ISP pipeline "
                     f"({len(built)} generated block(s), {self.spec.bit_depth}-bit)",
                source="the netlist in this design's pipeline.json",
            ),
            notes=self._address_map(),
        )
        modules.append((self.name, top["verilog"]))

        meta = {"nodes": [s.path for s in self.datapath],
                "generated": sorted(built), "nets": top["nets"]}
        name = self.name
        if control:
            wrapper = self._control_top(top, built)
            modules.append((wrapper["module"], wrapper["verilog"]))
            name = wrapper["module"]
            meta["control"] = {"bus": "axi4-lite", "addr_bits": wrapper["addr_bits"],
                               "commit": wrapper["commit"],
                               "regfile": wrapper["reg"]["module"]}

        return Generated(
            top=name,
            modules=tuple(modules),
            consumes=tuple(pipe_block.context_names()),
            latency=sum(r.latency for r in built.values()),
            meta=meta,
        )

    def _control_top(self, top: dict, built: dict) -> dict:
        """The register file, in front of the composed datapath.

        Everything structural here is np2hw's: revela hands it the register list
        it allocated and the binding it decided, and gets back the decode, the
        shadow registers and the commit. That the datapath top's parameter ports
        and the register names line up is not a coincidence to be maintained --
        both are built from :attr:`module_prefix` in one place.
        """
        from np2hw import control_wrap

        return control_wrap(
            top, self.address_map(), bind=self.control_bind(built),
            module_name=f"{self.name}_ctrl",
            addr_bits=self.allocator.address_bits(),
            header=spdx_header(
                what=f"{self.name} -- AXI4-Lite control register file and the "
                     f"{self.name} datapath",
                source="the register map allocated in revela/compose.py",
            ),
            notes=self._control_notes(),
        )

    def _control_notes(self) -> list[str]:
        """What a reviewer needs to know before reading the decode."""
        return [
            "// Every register below sits where revela's allocator put it -- one",
            f"// {BLOCK_ALIGN}-byte-aligned region per block INSTANCE, an identity word at",
            "// each region's base -- so address decode is a bit-slice compare and",
            "// the emitted JSON register map describes exactly this file.",
            "//",
            "// Writes land in a shadow register and are committed to the live value",
            "// at the frame boundary, so a frame is processed with one coherent set",
            "// of coefficients. A write during frame N therefore takes effect on",
            "// frame N+1; software that needs it sooner has to wait for the boundary,",
            "// not race it.",
            "//",
            "// A write to a read-only word, or to an unmapped address, is answered",
            "// SLVERR rather than silently dropped.",
        ]

    def _subsystem_of(self, path: str):
        """The subsystem an instance belongs to, if any."""
        prefix = path.split(".", 1)[0]
        return self.subsystem_instances.get(prefix)

    def _subsystem_module(self, name: str) -> str:
        return f"revela_{self.name}_{name}"

    def _boundary_domain(self) -> str:
        """The domain the pipeline's own stream ports carry.

        Taken from the block at the boundary rather than declared separately: the
        first block's input domain IS what the pipeline consumes.
        """
        for stage in self.datapath:
            if stage.block.inputs:
                return stage.block.inputs[0].domain
        return ""

    def _address_map(self) -> list[str]:
        """The address map and netlist, as comments a reviewer reads first."""
        lines = ["// Address map (see the emitted JSON for the authoritative version):"]
        for stage in self.stages:
            lines.append(f"//   0x{stage.instance.base:04x}  {stage.path:<24} "
                         f"{stage.paramset.block} v{stage.paramset.version[0]}."
                         f"{stage.paramset.version[1]}")
        lines.append("//")
        lines.append("// Netlist:")
        for source, sink in self.edges:
            tap = "  (tap: sink never stalls)" if self._is_tap(sink) else ""
            lines.append(f"//   {str(source):<28} -> {str(sink)}{tap}")
        return lines

    def _ports(self, built, stream) -> list:
        """Top-level ports: context, per-instance CSRs, then the pixel streams."""
        from np2hw import Port

        ports = [
            Port(f"ctx_{ctx.name}", "in", width=ctx.bits,
                 comment=f"Pipeline context, owned by `pipe` at base 0 and fanned "
                         f"out as a wire -- never duplicated per block. "
                         f"{ctx.description}")
            for ctx in pipe_block.CONTEXT
        ]
        for path, result in built.items():
            stage = self.stage(path)
            for name, bits in result.params:
                param = stage.paramset.param(name)
                ports.append(Port(
                    f"param_{stage.module_prefix}_{name}", "in", width=bits,
                    signed=param.signed,
                    comment=f"{path}.{name} @ "
                            f"0x{stage.instance.address_of(name):04x} "
                            f"({param.q_format}). {param.description}"))
        ports += [Port(name, "in", stream=stream) for name in self.inputs]
        ports += [Port(name, "out", stream=stream) for name in self.outputs]
        return ports

    def _instances(self, built) -> list:
        """One np2hw Instance per generated block, with its parameter bindings."""
        from np2hw import Instance

        instances = []
        for path, result in built.items():
            stage = self.stage(path)
            declared = [name for name, _, _ in result.core.interface["params"]]
            bind = {}
            for name in declared:
                if name in result.context_map:
                    bind[name] = (result.context_map[name],
                                  "pipeline context, not a register")
                else:
                    bind[name] = f"param_{stage.module_prefix}_{name}"
            # What each stream MEANS. Not derivable from the arithmetic -- a
            # 12-bit Bayer stream and a 12-bit luma stream are identical to a
            # compiler -- so the block declares it and the composer enforces it.
            domains = {port.name: port.domain
                       for port in stage.block.inputs + stage.block.outputs}
            instances.append(Instance(
                name=stage.module_prefix, core=result.core, bind=bind,
                domains=domains,
                comment=f"{path} (base 0x{stage.instance.base:04x})"))
        return instances

    def _connections(self, built) -> list:
        """Netlist edges, restricted to instances that were actually generated.

        A block that is declared but not yet built -- `stats`, whose accumulation
        np2hw cannot trace -- simply has no instance to wire to, so its edges are
        dropped. Its source's `ready` then ties high on its own, which is the
        right answer anyway: it was a tap.
        """
        from np2hw import Connection

        def endpoint(reference):
            if reference.node is None:
                return reference.port
            if reference.node not in built:
                return None
            return f"{self.stage(reference.node).module_prefix}.{reference.port}"

        edges = []
        for source, sink in self.edges:
            names = (endpoint(source), endpoint(sink))
            if None in names:
                continue
            edges.append(Connection(names[0], names[1]))
        return edges

# --------------------------------------------------------------------------- #
# Documentation, generated from the same map the host API reads
# --------------------------------------------------------------------------- #

def register_map_markdown(mapping: dict) -> str:
    """Render a register map as documentation.

    Generated from the emitted JSON rather than written alongside it, for the
    same reason the host API is: a hand-written register map is correct on the
    day it is written and slowly stops being correct afterwards, and the errors
    it acquires are exactly the ones nobody notices until hardware misbehaves.
    """
    out: list[str] = []
    a = out.append

    a(f"# {mapping['name']} register map")
    a("")
    a("Generated from the emitted register map JSON by "
      "`revela.compose.register_map_markdown`. Do not edit by hand.")
    a("")
    geometry, stream = mapping["geometry"], mapping["stream"]
    a(f"- Built for {geometry['width']}x{geometry['height']}, "
      f"{stream['bit_depth']}-bit, {stream['channels']}-channel "
      f"({stream['data_bits']}-bit data bus)")
    a(f"- {mapping['address_bits']}-bit address space, "
      f"blocks aligned to {mapping['block_align']} bytes")
    a(f"- Licence: {mapping['licence']}")
    a("")
    a("Every block starts on a power-of-two boundary, so address decode is a "
      "bit-slice compare rather than a range comparator. Nothing outside this "
      "file should hold an address.")
    a("")

    control = mapping.get("control")
    if control:
        a("## Control interface")
        a("")
        a(f"- {control['bus']}, {control['data_bits']}-bit data, "
          f"{control['address_bits']}-bit address")
        a(f"- Commit: {control['commit']}. {control['commit_description']}.")
        a(f"- {control['error_response']}.")
        a("- Read the `id_version` word of a block before writing anything to it: "
          "it is how software proves the bitstream matches this map.")
        a("")

    a("## Layout")
    a("")
    a("| Base | Instance | Block | Version | Size |")
    a("| --- | --- | --- | --- | --- |")
    for block in mapping["blocks"]:
        a(f"| `0x{block['base']:04x}` | `{block['path']}` | {block['block']} "
          f"| {block['version']} | {block['size']} B |")
    a("")

    for block in mapping["blocks"]:
        a(f"## `{block['path']}` — {block['block']} v{block['version']}")
        a("")
        if block["description"]:
            a(block["description"])
            a("")
        a(f"Base `0x{block['base']:04x}`, block ID `0x{block['id']:04x}`.")
        a("")
        if not block.get("implemented", True):
            a(f"> **Declared, not built yet:** {block['not_implemented_reason']}. "
              "The registers below are decoded and read back, and reach no "
              "datapath. Writing them has no effect on the image.")
            a("")
        if block["consumes"]:
            a(f"Consumes pipeline context, as wires from `pipe`: "
              + ", ".join(f"`{c}`" for c in block["consumes"]) + ".")
            a("")

        identity = block["id_version"]
        a(f"| Address | Offset | Register | Format | Reset | Access | Description |")
        a("| --- | --- | --- | --- | --- | --- | --- |")
        a(f"| `0x{identity['address']:04x}` | `+0x{identity['offset']:02x}` "
          f"| `id_version` | u32.0 | `0x{identity['value']:08x}` | ro "
          f"| {identity['description']} |")
        for register in block["registers"]:
            a(f"| `0x{register['address']:04x}` | `+0x{register['offset']:02x}` "
              f"| `{register['name']}` | {register['q_format']} "
              f"| {register['default']} | {register['access']} "
              f"| {register['description']} |")
        a("")

        for window in block["statistics"]:
            a(f"### `{window['name']}` statistics window")
            a("")
            a(window["description"])
            a("")
            a(f"- Base `0x{window['base']:04x}`, {window['size_bytes']} bytes "
              f"({window['buffering']}-buffered, {window['buffer_bytes']} bytes "
              f"per buffer)")
            a(f"- {window['records']} records of {window['record_words']} "
              f"word(s): " + ", ".join(f"`{f}`" for f in window["layout"]))
            a(f"- Access: {window['access']}. Read in bulk; the completed frame "
              "is in buffer 0 while the next accumulates in buffer 1.")
            a("")

    return "\n".join(out) + "\n"
