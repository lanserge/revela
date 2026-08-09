# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Profiles: parameter values for a pipeline, and the sensor they were tuned for.

A profile is what lets ONE pipeline description serve several sensors and several
tunings. The structure stays fixed; only the numbers change.

    from revela.profiles import load, resolve
    from revela.designs import load as load_pipeline

    profile = load("pipelines/mono/imx219/basic/profiles/indoor.json")
    pipeline = load_pipeline("pipelines/mono/imx219/basic/pipeline.json")
    settings = resolve(profile, pipeline)
    settings.apply(device)

Three files, three jobs
-----------------------

    pipeline description   structure   which blocks, in what order
    profile                values      what to write into their registers
    register map           addresses   generated output, never hand-written

A profile carries no structure and no addresses. It cannot add a block, reorder
the datapath, or say where a register lives -- the schema rejects `blocks` and
`addresses` outright. It can only set values for registers the pipeline's blocks
already declare, and every value is checked against the declared width,
signedness and range at resolve time, so a bad number fails at load rather than
on hardware.

Precedence
----------

Three sources can speak to the same register, and the order matters:

    1. the block's declared RESET value          (always present)
    2. values DERIVED from the sensor            (unless derive_from_sensor is false)
    3. the profile's EXPLICIT values             (highest)

Which is to say: the block knows a safe starting point, the sensor knows what its
own pedestal and CFA order are, and the profile knows what somebody measured on a
real camera. Each layer only overrides the one below where it genuinely knows
better. :class:`Settings` records which layer each value came from, because
"where did this number come from" is the first question anyone asks of a tuning
file that is producing a strange picture.

Keying
------

Values are keyed by block INSTANCE PATH (``left.blacklevel``) or by BLOCK NAME
(``blacklevel``). A block-name key applies to every instance of that block, which
is how a stereo pipeline shares one tuning between two eyes; an instance-path key
overrides it for that instance, which is how one eye gets corrected for a
difference in its own optics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.json"

# Where a resolved value came from, in increasing order of authority.
FROM_DEFAULT = "default"
FROM_SENSOR = "sensor"
FROM_PROFILE = "profile"


@lru_cache(maxsize=1)
def schema() -> dict:
    """The profile schema, loaded once."""
    return json.loads(SCHEMA_PATH.read_text())


def validate(profile: dict) -> None:
    """Raise if ``profile`` does not satisfy the schema."""
    import jsonschema

    jsonschema.validate(instance=profile, schema=schema())


def load(path: str | Path, validate_profile: bool = True) -> dict:
    """Load a profile, validating it by default.

    The ``pipeline`` path, if present, is resolved relative to the profile file
    and rewritten to an absolute path, so a profile can be loaded from anywhere.
    """
    path = Path(path)
    profile = json.loads(path.read_text())
    if validate_profile:
        validate(profile)
    if "pipeline" in profile:
        profile["pipeline"] = str((path.parent / profile["pipeline"]).resolve())
    return profile


def pipeline_for(profile: dict):
    """Build the pipeline this profile names.

    Raises:
        KeyError: if the profile does not name one. A profile is tuning for a
            pipeline, not a pipeline, so this is a real error rather than
            something to guess around.
    """
    from revela import designs

    if "pipeline" not in profile:
        raise KeyError(
            f"profile {profile['name']!r} does not name a pipeline description; "
            "build the pipeline yourself and pass it to resolve()")
    return designs.load(profile["pipeline"])


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Settings:
    """Every register value for a pipeline, and where each one came from.

    Attributes:
        values: ``{instance_path: {register: value}}``, complete -- every
            register of every block, not just the ones the profile mentioned.
        origin: ``{instance_path: {register: source}}``, one of ``default``,
            ``sensor`` or ``profile``.
        sensor: the sensor description this was resolved against.
        control: the profile's 3A tuning, ready to pass to the control loops.
        name: the profile's name.
    """

    name: str
    values: dict[str, dict[str, int]]
    origin: dict[str, dict[str, str]]
    sensor: dict
    control: dict = field(default_factory=dict)

    def for_block(self, path: str) -> dict[str, int]:
        try:
            return self.values[path]
        except KeyError:
            raise KeyError(
                f"no block instance {path!r} in these settings; they cover "
                f"{sorted(self.values)}") from None

    def sources(self, source: str) -> dict[str, dict[str, int]]:
        """Only the values that came from a given layer.

        Useful when a picture looks wrong and the question is whether the
        profile is doing something, or whether it is all still at reset.
        """
        out: dict[str, dict[str, int]] = {}
        for path, registers in self.origin.items():
            chosen = {name: self.values[path][name]
                      for name, where in registers.items() if where == source}
            if chosen:
                out[path] = chosen
        return out

    def apply(self, device) -> int:
        """Write every value through a host device. Returns the number written."""
        written = 0
        for path, registers in self.values.items():
            block = device.block(path)
            for name, value in registers.items():
                setattr(block, name, value)
                written += 1
        return written

    def summary(self) -> str:
        lines = [f"profile {self.name!r} for sensor "
                 f"{self.sensor['name']} ({self.sensor['vendor']})"]
        for path in sorted(self.values):
            counts: dict[str, int] = {}
            for where in self.origin[path].values():
                counts[where] = counts.get(where, 0) + 1
            detail = ", ".join(f"{n} from {w}" for w, n in sorted(counts.items()))
            lines.append(f"  {path:<20} {detail}")
        return "\n".join(lines)


def resolve(profile: dict, pipeline) -> Settings:
    """Layer reset values, sensor-derived values and the profile into Settings.

    Every value is checked against the declaring Param's range as it is applied,
    so a profile that would overflow a register fails here -- naming the register
    and quoting its description -- rather than being masked into something
    plausible on the way to the hardware.
    """
    from revela import sensors

    sensor = sensors.load(profile["sensor"]["name"])
    mode = profile["sensor"].get("mode")

    values: dict[str, dict[str, int]] = {}
    origin: dict[str, dict[str, str]] = {}

    # Layer 1: the block's declared reset values. Always complete, so the result
    # describes the whole pipeline rather than only what somebody remembered.
    for stage in pipeline.stages:
        values[stage.path] = dict(stage.paramset.defaults())
        origin[stage.path] = {name: FROM_DEFAULT for name in values[stage.path]}

    # Layer 2: what the sensor description implies. A block opts in by exposing
    # from_sensor(); nothing else needs to know which blocks those are.
    if profile.get("derive_from_sensor", True):
        for stage in pipeline.stages:
            derive = stage.block.sensor_hook
            if derive is None:
                continue                     # this block takes nothing from a sensor
            for name, value in derive(sensor, mode).items():
                _assign(stage, values, origin, name, value, FROM_SENSOR, profile)

    # Layer 3: the profile's explicit values. The block-name key is applied
    # first and the instance-path key second, so one eye of a stereo pair can
    # override the tuning both eyes share.
    declared = profile.get("values", {})
    for stage in pipeline.stages:
        keys = [stage.paramset.block]
        if stage.path != stage.paramset.block:
            keys.append(stage.path)
        for key in keys:
            for name, value in declared.get(key, {}).items():
                _assign(stage, values, origin, name, value, FROM_PROFILE, profile)

    _check_keys_were_used(profile, pipeline)
    _check_geometry_fits(profile, pipeline, values, origin)

    return Settings(name=profile["name"], values=values, origin=origin,
                    sensor=sensor, control=profile.get("control", {}))


def _assign(stage, values, origin, name, value, source, profile) -> None:
    """Set one register, checking it against what the block declared."""
    if name not in values[stage.path]:
        raise KeyError(
            f"profile {profile['name']!r}: block {stage.path!r} has no register "
            f"{name!r}; it declares {sorted(values[stage.path])}")
    param = stage.paramset.param(name)
    low, high = param.limits
    if not low <= int(value) <= high:
        raise ValueError(
            f"profile {profile['name']!r}: {stage.path}.{name} = {value} is outside "
            f"[{low}, {high}] for a {param.bits}-bit "
            f"{'signed' if param.signed else 'unsigned'} register ({param.q_format}). "
            f"{param.description}")
    values[stage.path][name] = int(value)
    origin[stage.path][name] = source


def _check_geometry_fits(profile: dict, pipeline, values, origin) -> None:
    """Refuse a profile whose sensor mode is larger than the pipeline was built for.

    Resolution BELOW the built size is free -- that is what the context registers
    are for. Above it is not: line buffers are sized at synthesis, and a width
    beyond them is not something a register can fix. This is the one place a
    profile can meaningfully conflict with its pipeline, so it is checked rather
    than discovered as a torn image on hardware.

    Only DELIBERATELY CHOSEN values are checked -- those from the sensor or from
    the profile. A block's reset default is a power-on value that says nothing
    about what this build can do (``pipe`` defaults to 1920 whatever geometry it
    was built for), and failing on one would make every small pipeline
    unresolvable for no reason.
    """
    context = values.get("pipe")
    if not context:
        return
    chosen = origin.get("pipe", {})
    for axis, built in (("width", pipeline.width), ("height", pipeline.height)):
        if chosen.get(axis) == FROM_DEFAULT:
            continue
        wanted = context.get(axis)
        if wanted is not None and wanted > built:
            raise ValueError(
                f"profile {profile['name']!r} selects sensor mode "
                f"{profile['sensor'].get('mode') or 'default'!r} needing "
                f"{axis}={wanted}, but the pipeline is built for {axis}={built}. "
                f"Line buffers are sized at synthesis: a smaller mode is free, a "
                f"larger one needs a pipeline built for it.")


def _check_keys_were_used(profile: dict, pipeline) -> None:
    """Refuse a profile that names a block the pipeline does not contain.

    Silently ignoring an unknown key is how a profile ends up half-applied after
    a block is renamed, with nothing to show for it but a picture that looks
    slightly wrong.
    """
    known = {stage.path for stage in pipeline.stages}
    known |= {stage.paramset.block for stage in pipeline.stages}
    unknown = sorted(set(profile.get("values", {})) - known)
    if unknown:
        raise KeyError(
            f"profile {profile['name']!r} sets values for {unknown}, which this "
            f"pipeline does not contain. It has instances {sorted(s.path for s in pipeline.stages)} "
            f"of blocks {sorted({s.paramset.block for s in pipeline.stages})}")
