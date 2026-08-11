# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""ISP blocks: one file per block, one model per block.

Every block in this package follows the same shape, so :mod:`revela.compose`
can compose them without knowing what any of them do -- including ``pipe``,
which is an ordinary block and not a special case:

    ``.params``   the :class:`revela.params.ParamSet` the decorator built
                  from the declarations, with LOCAL offsets only. The block
                  never sees an absolute address; :mod:`revela.compose`
                  assigns a base per INSTANCE. There is no module-level
                  ``PARAMS`` alias: the decorated block object owns its
                  register set, and a second name for it is a second place.

    the model     the decorated function itself, written at the hardware's
                  arithmetic: the specification and the golden reference.
                  ``Block.run()`` runs it over a frame with validation DERIVED
                  from the declarations -- there is no per-block adapter,
                  because three copies of one function was three copies of one
                  function. No float reference model exists anywhere in this
                  package: the hardware algorithm is a different algorithm,
                  not a quantised float one, and comparing them would measure
                  the distance between two algorithms and call it error.

    ``generate()`` emits the Verilog, returning a :class:`Generated`.

Blocks that need pipeline context (width, height, active window, Bayer phase,
bit depth) name it in ``consumes``; it arrives as a wire from the ``pipe``
block, never as a copy in the block's own register map.
"""
from __future__ import annotations

import importlib
import pkgutil
import textwrap
from dataclasses import dataclass, field
from types import ModuleType


# Stream domains. What a stream MEANS, which its width and framing cannot say:
# a 12-bit Bayer stream and a 12-bit luma stream are indistinguishable to a
# compiler and nonsense to connect. Declared by the block, because it is not
# derivable from the arithmetic -- unlike the width, which is.
BAYER = "bayer"     # one colour per pixel, identity given by CFA position
BAYER_G = "bayer+g"  # the raw sample plus reconstructed green: the word a
                     # two-stage demosaic passes between its stages
RGB = "rgb"         # three components per pixel, sensor or display primaries
YUV = "yuv"         # luma plus chroma
LUMA = "luma"       # luma alone, e.g. the sharpening path
STATS = "stats"     # a stream consumed for measurement, not for display

# How many components a pixel of each domain carries. A block declares its
# domain; the component count follows, so no block repeats it as its own guard.
DOMAIN_CHANNELS = {BAYER: 1, BAYER_G: 2, LUMA: 1, STATS: 1,
                   RGB: 3, YUV: 3}


@dataclass(frozen=True)
class StreamPort:
    """One stream port of a block: its name and what it carries.

    The WIDTH is deliberately absent. It is a consequence of the model's
    arithmetic and np2hw derives it from the trace; declaring it here as well
    would be a second source of truth free to disagree with the first. The
    domain is the opposite case -- nothing in the arithmetic says whether these
    pixels are Bayer or RGB, so the block must say.
    """

    name: str
    domain: str
    description: str = ""


@dataclass(frozen=True)
class ContextBit:
    """A single-bit slice of a pipeline context register, taken by the model.

    A traced model wants the Bayer phase as two one-bit values it can use as
    slice starts; the pipeline owns it as one two-bit context register. This
    says which bit feeds which model argument, so the composer can wire it and
    nobody writes a wrapper to split it.
    """

    name: str                 # the model argument, and the generated param port
    context: str              # the pipeline context signal it comes from
    bit: int
    description: str = ""

    @property
    def expression(self) -> str:
        return f"ctx_{self.context}[{self.bit}]"

    def to_np2hw(self):
        """The one-bit np2hw Param the traced model uses as a slice phase."""
        from np2hw import Param as Np2hwParam

        return Np2hwParam(self.name, bits=1, description=self.description)


@dataclass(frozen=True)
class Ports:
    """The stream ports a block presents, by name.

    Declared rather than assumed, because "one in, one out" is only true of the
    simplest blocks. ``stats`` consumes a stream and produces none -- it observes
    the datapath rather than sitting in it, which a linear chain cannot express
    and which is exactly the distinction that decides whether a connection needs
    to carry backpressure.

    A block with no outputs is a SINK. A sink must never stall its source: it is
    watching a stream that has somewhere else to be, so it accepts a pixel every
    cycle and its ``ready`` is not wired back.
    """

    inputs: tuple[str, ...] = ("in",)
    outputs: tuple[str, ...] = ("out",)

    @property
    def is_sink(self) -> bool:
        return not self.outputs

    def has(self, port: str) -> bool:
        return port in self.inputs or port in self.outputs

    def direction(self, port: str) -> str:
        if port in self.inputs:
            return "in"
        if port in self.outputs:
            return "out"
        raise KeyError(
            f"no port {port!r}; this block has inputs {list(self.inputs)} and "
            f"outputs {list(self.outputs)}")


# The default for an ordinary inline block: consume a stream, produce a stream.
INLINE = Ports()

# A block that observes the datapath and produces no pixels.
MONITOR = Ports(inputs=("in",), outputs=())

# A block with no stream ports at all -- configuration only, like `pipe`.
NO_STREAM = Ports(inputs=(), outputs=())


@dataclass(frozen=True)
class Generated:
    """The Verilog one block generated, plus what a composer needs to wire it.

    Args:
        top: name of the module a pipeline should instantiate.
        modules: ``(module_name, verilog_text)`` in dependency order, so writing
            them out in sequence gives a file that elaborates.
        params: ``(scalar_register_name, bits)`` the top module expects on its
            ``param_*`` inputs, in register-map order.
        consumes: pipeline context signals the top module expects as wires.
        context_map: ``{param_name: verilog_expr}`` for generated ``param_*``
            inputs that are driven by pipeline CONTEXT rather than by a register.
            A Bayer phase is context, not configuration -- one owner, fanned out
            -- so the composer wires it straight from `pipe` instead of giving
            the block a CSR that would be a second copy of it.
        latency: pixels of delay from input to output, for pipeline alignment.
        meta: generator detail worth keeping (line buffers, accumulator width).
    """

    top: str
    modules: tuple[tuple[str, str], ...]
    params: tuple[tuple[str, int], ...] = ()
    consumes: tuple[str, ...] = ()
    context_map: dict = field(default_factory=dict)
    # The np2hw result this block was generated from. It carries the module's
    # own interface -- which framing flags it accepts, which parameter ports it
    # has -- so the composer reads that from the generator rather than revela
    # keeping a second description of it that can drift.
    core: dict | None = None
    latency: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def verilog(self) -> str:
        """All modules concatenated, in elaboration order."""
        return "\n\n".join(text.rstrip() for _, text in self.modules) + "\n"

    def module_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.modules)


class _View:
    """Attribute access over a set of values, for the model's `p` and `ctx`."""

    __slots__ = ("_values", "_decl")

    def __init__(self, values):
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, name):
        if name == "decl":
            try:
                return object.__getattribute__(self, "_decl")
            except AttributeError:
                pass
        try:
            return object.__getattribute__(self, "_values")[name]
        except KeyError:
            raise AttributeError(
                f"{name!r} is not available here; this view has "
                f"{sorted(object.__getattribute__(self, '_values'))}") from None


@dataclass
class Block:
    """An ISP block: one model function, plus what cannot be derived from it.

    Produced by :func:`ispblock`. The model is the specification; everything
    here is a declaration the arithmetic cannot supply -- the register set, what
    the streams MEAN, which context bits the model takes. Notably absent are the
    stream WIDTHS, which np2hw derives from the trace; declaring them here too
    would be a second source of truth free to disagree with the first.
    """

    model: object
    params: "ParamSet"
    inputs: tuple
    outputs: tuple
    context: tuple
    # Register-attribute overrides this VARIANT was configured with, empty on
    # the base block. {"gain": {"frac": 12}} -- the same dict the design JSON
    # carries and describe() recovers, so a variant knows its own recipe.
    overrides: dict = field(default_factory=dict)
    # Why this block cannot be traced yet, if it cannot. Empty means it can.
    # Stated as a REASON rather than a flag, because "declared but not built" is
    # a fact about np2hw's current reach and the reader deserves to know which.
    not_traceable: str = ""
    # Optional hook: register values this block's sensor description implies.
    # Registered with @<block>.sensor_values so it lives on the block rather
    # than being fished out of the defining module by name.
    sensor_hook: object = None

    @property
    def name(self) -> str:
        return self.params.block

    @property
    def ports(self) -> Ports:
        return Ports(inputs=tuple(port.name for port in self.inputs),
                     outputs=tuple(port.name for port in self.outputs))

    def domain(self, port: str) -> str:
        for declared in self.inputs + self.outputs:
            if declared.name == port:
                return declared.domain
        raise KeyError(
            f"block {self.name!r} has no stream port {port!r}")

    def __call__(self, *args, **kwargs):
        """Run the model. A Block is the function it decorates."""
        if self.model is None:
            raise TypeError(
                f"block {self.name!r} is configuration only and has no model")
        return self.model(*args, **kwargs)

    @property
    def traceable(self) -> bool:
        return self.model is not None and not self.not_traceable

    @property
    def implemented(self) -> bool:
        """Whether this block's declaration reaches hardware.

        A block with a datapath reaches hardware only if it can be traced. A
        block with no stream ports has no datapath to trace -- ``pipe`` owns
        configuration and its registers reach the blocks as context wires -- so
        there is nothing left for it to be missing.

        The distinction is not cosmetic. Every declared register is decoded by
        the register file, because the register map and the decode must be the
        same thing; but a register belonging to a block that was never built
        reads back what was written and drives nothing. Software is told which
        is which rather than left to discover it from an image that did not
        change.
        """
        return self.traceable or not (self.inputs or self.outputs)

    def configure(self, registers: dict | None = None) -> "Block":
        """The variant of this block with register attributes overridden.

        ``registers`` is ``{register_name: {attribute: value}}``, and only
        attributes the declaration lists as ``configurable`` are accepted --
        the whitelist is the block author's statement of what varies between
        variants, and everything else is structure.

        Variants are CACHED per canonical override set, so two instances
        configured identically share one Block object. That object identity
        is what downstream type grouping keys on: the register map and the
        SystemRDL emit one type per variant, never one per instance and
        never one type lying about two different Q formats.
        """
        import dataclasses

        if not registers:
            return self
        base = getattr(self, "_base", self)
        def hashable(value):
            return tuple(value) if isinstance(value, (list, tuple)) else value

        key = tuple(sorted(
            (reg, tuple(sorted((attr, hashable(v))
                               for attr, v in attrs.items())))
            for reg, attrs in registers.items()))
        cache = base.__dict__.setdefault("_variants", {})
        if key in cache:
            return cache[key]

        from revela.params import ParamSet

        declared = {param.name: param for param in base.params.params}
        rebuilt = []
        for param in base.params.params:
            attrs = registers.get(param.name)
            if not attrs:
                rebuilt.append(param)
                continue
            refused = set(attrs) - set(param.configurable)
            if refused:
                raise ValueError(
                    f"block {base.name!r}: register {param.name!r} does not "
                    f"allow overriding {sorted(refused)}; it allows "
                    f"{sorted(param.configurable) or 'nothing'}")
            changes = {k: (tuple(int(x) for x in v) if k == "shape"
                           else int(v))
                       for k, v in attrs.items()}
            if param.default_unity or (param.default_ramp
                                       and not attrs.keys() - {"bits", "shape"}):
                # replace() would carry an OLD resolved default forward;
                # zero it so __post_init__ / leaf() re-derive for the new
                # frac, bits or shape.
                changes.setdefault("default", 0)
            rebuilt.append(dataclasses.replace(param, **changes))
        unknown = set(registers) - set(declared)
        if unknown:
            raise KeyError(
                f"block {base.name!r} declares no register named "
                f"{sorted(unknown)}; it declares {sorted(declared)}")

        variant = dataclasses.replace(
            base,
            params=ParamSet(block=base.params.block,
                            version=base.params.version,
                            params=rebuilt, stats=list(base.params.stats),
                            consumes=base.params.consumes,
                            description=base.params.description),
            overrides={reg: dict(attrs) for reg, attrs in registers.items()},
        )
        variant.__dict__["_base"] = base
        cache[key] = variant
        return variant

    @property
    def variant_signature(self) -> str:
        """A stable, Verilog-safe suffix naming this variant's overrides.

        Empty for the base block. Used wherever a TYPE needs a distinct name
        -- the SystemRDL regfile, the shared-module cache -- because two
        variants merging into one type is the map lying about one of them.
        """
        if not self.overrides:
            return ""
        parts = []
        for register in sorted(self.overrides):
            for attr, value in sorted(self.overrides[register].items()):
                flat = ("x".join(str(int(x)) for x in value)
                        if isinstance(value, (tuple, list)) else value)
                parts.append(f"{register}_{attr}{flat}")
        return "__" + "_".join(parts)

    def sensor_values(self, function):
        """Register the hook that turns a sensor description into register values.

            @blacklevel.sensor_values
            def from_sensor(sensor, mode_name=None): ...

        Returns the function unchanged, so it stays callable under its own name.
        """
        self.sensor_hook = function
        return function

    # -- generation ----------------------------------------------------------- #

    def run(self, pixel, values: dict | None = None, bit_depth: int = 12,
            **context):
        """Run the model over a whole frame, validated from the DECLARATIONS.

        The per-block adapters this replaces were three copies of the same
        function, each re-stating facts the Block already declares: which
        context registers it consumes, and whether it is CFA-indexed. Now
        the declaration drives the validation -- a block that consumes
        ``bayer_phase`` gets the even-dimension check and the phase range
        check; one that does not, does not -- and a new block gets the whole
        adapter by existing.

        Args:
            pixel: ``(height, width)`` frame, unsigned, ``bit_depth`` bits.
            values: register values for ``params.bind`` (defaults fill in).
            bit_depth: datapath width.
            **context: pipeline context by name, e.g. ``bayer_phase=2``.
                Everything the block consumes defaults to 0; a name the
                block does not consume is refused, because silently
                accepting it would be accepting a typo.
        """
        import numpy as np

        from revela.blocks import pipe as pipe_block

        if self.model is None:
            raise TypeError(
                f"block {self.name!r} is configuration only and has no model")
        pixel = np.asarray(pixel)
        in_channels = (DOMAIN_CHANNELS.get(self.inputs[0].domain, 1)
                       if self.inputs else 1)
        if in_channels == 1:
            if pixel.ndim != 2:
                raise ValueError(
                    f"expected a 2-D frame, got shape {pixel.shape}")
        elif pixel.ndim != 3 or pixel.shape[-1] != in_channels:
            # A model speaks CHANNELS ((h, w, c)); words exist on the wire
            # only, and StreamSpec owns the translation.
            raise ValueError(
                f"block {self.name!r} consumes {self.inputs[0].domain!r}: "
                f"expected an (h, w, {in_channels}) frame, got shape "
                f"{pixel.shape}")
        consumed = set(self.params.consumes)
        unknown = set(context) - consumed
        if unknown:
            raise TypeError(
                f"block {self.name!r} does not consume {sorted(unknown)}; "
                f"it consumes {sorted(consumed) or 'no context'}")
        ctx_values = {}
        for name in consumed:
            declared = pipe_block.resolve(name)
            value = int(context.get(name, 0))
            lo, hi = 0, (1 << declared.bits) - 1
            if not lo <= value <= hi:
                raise ValueError(
                    f"context {name!r} = {value} outside its declared "
                    f"{declared.bits}-bit range")
            ctx_values[name] = value
        if any(bit.context == "bayer_phase" for bit in self.context):
            height, width = pixel.shape[:2]
            if height % 2 or width % 2:
                raise ValueError(
                    f"frame {width}x{height} has an odd dimension; a "
                    "CFA-indexed block has no consistent Bayer phase unless "
                    "both dimensions are even")
        bound = self.params.bind(values or {})
        return self.model(pixel, bound, self.context_view(ctx_values),
                          bit_depth)

    def context_view(self, values: dict):
        """The `ctx` argument for a NumPy run, from context register values."""
        return _View({bit.name: (int(values[bit.context]) >> bit.bit) & 1
                      for bit in self.context})

    def generate(self, spec, width: int, height: int, module_name: str) -> "Generated":
        """Trace the model into Verilog. Generic: every block uses this one.

        There is no per-block generator and no hand-written Verilog anywhere in
        this package. A block is its model plus its declarations, and this turns
        the pair into a module.
        """
        from np2hw import Image2D, to_ir, generate as np2hw_generate

        if not self.traceable:
            raise NotImplementedError(
                f"block {self.name!r} is declared but not built: "
                f"{self.not_traceable}")
        expected = DOMAIN_CHANNELS.get(self.inputs[0].domain) if self.inputs else None
        if expected is not None and spec.channels != expected:
            raise ValueError(
                f"block {self.name!r} consumes {self.inputs[0].domain!r}, which is "
                f"{expected} component(s) per pixel; got a {spec.channels}-channel "
                "stream. The domain is declared on the port, so this is a wiring "
                "error rather than something the block should special-case.")
        if len(self.inputs) != 1 or len(self.outputs) > 1:
            raise NotImplementedError(
                f"block {self.name!r} has {len(self.inputs)} inputs and "
                f"{len(self.outputs)} outputs; tracing handles one stream in and "
                "at most one out. A block that merges or splits streams needs "
                "np2hw to trace multiple images first.")

        # The traced image is the packed DATA WORD, not one component: a
        # 3-channel block sees channels side by side in one value and
        # unpacks them itself, exactly as the wire carries them.
        image = Image2D("pixel", width, height, bits=spec.data_bits, signed=False)
        context = [bit.to_np2hw() for bit in self.context]
        registers = [param.to_np2hw() for param in self.params]

        def traced(pixel, *traced_args):
            from revela.params import Declarations

            ctx = _View(dict(zip((bit.name for bit in self.context),
                                 traced_args[:len(context)])))
            values = _View(dict(zip((param.name for param in self.params),
                                    traced_args[len(context):])))
            # The same declaration access the NumPy run has: a model whose
            # arithmetic depends on a declared attribute (a Q format's shift)
            # reads it from the ONE configured Param, in both modes.
            object.__setattr__(values, "_decl",
                               Declarations(self.params.params))
            return self.model(pixel, values, ctx, spec.bit_depth)

        _, result = to_ir(traced, image, *context, *registers,
                          channels=expected or 1)
        core = np2hw_generate(result, module_name=module_name)

        # The emitter states the word layout it built; the stream declares
        # what this block's output means. They must be the same statement.
        out_meta = core["interface"].get("output") or {}
        out_domain = self.outputs[0].domain if self.outputs else None
        out_channels = DOMAIN_CHANNELS.get(out_domain, 1)
        if out_channels > 1:
            if (out_meta.get("channels", 1) != out_channels
                    or out_meta.get("field_bits") != spec.bit_depth):
                raise ValueError(
                    f"block {self.name!r} declares {out_channels} channels of "
                    f"{spec.bit_depth} bits, but the generated core packed "
                    f"{out_meta.get('channels', 1)} field(s) of "
                    f"{out_meta.get('field_bits')}; the wire and the "
                    "declaration have diverged")

        verilog = "\n".join([
            *spdx_header(
                what=f"{self.name} -- {self.params.description} "
                     f"({spec.bit_depth}-bit datapath)",
                source=f"{self.model.__name__}() in "
                       f"revela/blocks/{self.name}.py, traced by np2hw",
            ),
            "",
            core.verilog,
        ])
        return Generated(
            top=module_name,
            modules=((module_name, verilog),),
            params=tuple((r.name, r.param.bits) for r in self.params.registers),
            consumes=self.params.consumes,
            context_map={bit.name: bit.expression for bit in self.context},
            core=core,
            latency=1,
            meta={"line_buffers": core.line_buffers,
                  "shift_depth": core.shift_depth,
                  "out_bits": core.out_bits, "bit_depth": spec.bit_depth},
        )

def configblock(name, *, version, description, params=(), stats=()):
    """A block with registers but no pixel stream -- `pipe`, and nothing else yet.

    Not a special case in the composer: it is allocated an address like any other
    block and simply has no ports to wire.
    """
    from revela.params import ParamSet

    return Block(
        model=None,
        params=ParamSet(block=name, version=version, params=list(params),
                        stats=list(stats), description=description),
        inputs=(), outputs=(), context=(),
        not_traceable="it owns configuration and has no datapath to trace",
    )


def ispblock(*, version, description, name=None, params=(), stats=(),
             consumes=(), inputs=(), outputs=(), context=(), not_traceable=""):
    """Declare an ISP block. The decorated function IS the block's model.

        @ispblock(
            version=(1, 0),
            description="Per-CFA-colour black level offset with saturation.",
            inputs=(StreamPort("in", BAYER),),
            outputs=(StreamPort("out", BAYER),),
            consumes=("bayer_phase",),
            context=(ContextBit("phase_row", "bayer_phase", bit=1), ...),
            params=[Param("offset", ...)],
        )
        def blacklevel(pixel, p, ctx, bit_depth):
            ...

    The block's name is the function's name. The model takes the pixel stream,
    a parameter view, a context view and the datapath width, and it is called
    with real arrays when it is the reference and with traced values when it is
    the hardware -- one function, both roles.

    Everything declared here is something the arithmetic cannot say. Stream
    widths are not declared: np2hw derives them from the trace.
    """
    from revela.params import ParamSet

    def decorate(function):
        return Block(
            model=function,
            params=ParamSet(block=name or function.__name__, version=version,
                            params=list(params), stats=list(stats),
                            consumes=tuple(consumes), description=description),
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            context=tuple(context),
            not_traceable=not_traceable,
        )

    return decorate


def registry() -> dict[str, Block]:
    """Every declared :class:`Block`, keyed by name.

    Discovered rather than listed, so a new block is available to a pipeline
    description as soon as it is declared -- there is no second place to register
    it and therefore no way for the two to disagree. Stub blocks are not declared
    as Blocks and so do not appear: a pipeline cannot name a block that has no
    model and no register interface.
    """
    found: dict[str, Block] = {}
    for info in pkgutil.walk_packages(__path__, prefix=f"{__name__}."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if isinstance(value, Block):
                found[value.name] = value
    return found


def resolve(name: str) -> Block:
    """The :class:`Block` for a name, or an error listing what exists."""
    available = registry()
    try:
        return available[name]
    except KeyError:
        raise KeyError(
            f"no block named {name!r}; declared blocks are {sorted(available)}. "
            "Blocks that are declared stubs have no model yet and cannot be "
            "composed.") from None


def comment(text: str, indent: str = "    ", width: int = 78) -> list[str]:
    """Wrap a parameter description into Verilog comment lines.

    Rule 4 says each parameter's description is carried through from its
    declaration into the generated Verilog. Carried through means ALL of it:
    truncating to the first clause drops precisely the part a reviewer needs
    (what to write, what the reset value means), which is the part that cannot
    be guessed from the signal name.
    """
    lines = textwrap.wrap(" ".join(text.split()), width=width - len(indent) - 3)
    return [f"{indent}// {line}" for line in lines] or [f"{indent}//"]


def spdx_header(what: str, source: str) -> list[str]:
    """The header every generated Verilog file carries.

    Generated Verilog carries this project's licence because its datapath is
    derived from the model it was traced from. The structure np2hw emits around
    it is covered by that project's Output Exception and carries no obligation,
    so the licence on the file comes from the model alone. Somebody licensing
    this will read the output in review, so the header says what produced it and
    from what.
    """
    return [
        "// Copyright 2026 Serge Rabyking",
        "// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1",
        "//",
        f"// {what}",
        f"// Generated by revela from {source}. Do not edit by hand:",
        "// the NumPy model is the specification, and the tests hold this file",
        "// bit-exactly equal to it.",
    ]
