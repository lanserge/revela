# pipelines

One directory per pipeline **design**, filed by topology, sensor and variant:

```
pipelines/<topology>/<sensor>/<variant>/
```

```
pipelines/
  mono/
    imx219/
      basic/
        pipeline.json            structure   which blocks, in what order
        profiles/
          indoor.json            values      tuning for one scene
          outdoor.json           values      same structure, different numbers
        build/                   generated   .v, register map, register docs
  stereo/
    imx219/
      basic/
        pipeline.json
        profiles/default.json
```

`basic` is the baseline variant for a given topology and sensor: the smallest
useful datapath, and the thing every other variant is a change against. Later
variants sit alongside it under their own names.

`build/` is generated and git-ignored. The Verilog, the register map and the
register-map documentation all come out of `pipeline.json` plus the block
declarations; committing them would create a second source of truth that starts
going stale immediately.

## The layout repeats what the JSON says, so tests check they agree

Topology and sensor appear both in the path and inside `pipeline.json`. Anything
stated twice can disagree, so `tests/test_pipeline_description.py` enforces it:
a design under `stereo/` must describe at least two streams, a design under
`imx219/` must declare that sensor, every profile under it must be tuned for it,
and the description's `name` must be `revela_<topology>_<sensor>_<variant>` so
module names stay unique when several are built side by side.

A design must also have at least one profile. A pipeline with no tuning has never
been run against anything.

## Subsystems

A design that runs the same chain more than once describes it once:

```json
"subsystems": [
  { "name": "eye",
    "inputs":  [{ "name": "sensor" }],
    "outputs": [{ "name": "video" }],
    "nodes": [{ "instance": "blacklevel", "block": "blacklevel" }, ...],
    "connections": [{ "from": "sensor", "to": "blacklevel.in" }, ...] }
],
"nodes": [
  { "instance": "left",  "subsystem": "eye" },
  { "instance": "right", "subsystem": "eye" }
]
```

**Addresses are still allocated per INSTANCE.** A `blacklevel` inside subsystem
instance `left` is the instance `left.blacklevel`, with its own registers,
reachable from the host as `dev.left.blacklevel`. The register map is
byte-identical to spelling the graph out twice -- there is a test for exactly
that, because it is the property that makes subsystems safe to adopt. What
changes is the emitted RTL: **one** block module instantiated per eye, instead of
two identical copies.

A subsystem's boundary port (`left.sensor`) is a NAME, not a component. It joins
the edge into it and the edge out of it into one connection, so the pipeline
graph stays flat and address allocation, the register map and the host API need
to know nothing about subsystems at all.

## Three kinds of JSON

| File | Direction | Holds | Schema |
| --- | --- | --- | --- |
| `pipeline.json` | input | structure — blocks and order | `revela/designs/schema.json` |
| `profiles/*.json` | input | values — registers, 3A tuning | `revela/profiles/schema.json` |
| `build/*.json` | **output** | addresses — the register map | generated |

Neither input can express an address. Blocks declare local offsets and revela
assigns each block instance a base at composition time; a description or profile
that could pin one would be a second copy of the address map, free to disagree
with the hardware. Both schemas reject an `addresses` key outright.

A profile additionally cannot change structure — no blocks, no reordering — so a
tuning file can never alter the gateware. That is what makes "the same pipeline
in a different scene" a matter of swapping one file.

## Building one

```bash
python examples/build_pipeline.py pipelines/mono/imx219/basic/pipeline.json
python examples/build_pipeline.py pipelines/mono/imx219/basic/pipeline.json \
    --profile pipelines/mono/imx219/basic/profiles/indoor.json
```

Output lands in that design's `build/`.

## Adding a design

Create `pipelines/<topology>/<sensor>/<variant>/`, write `pipeline.json` against
the schema, and add at least one profile. The tests discover everything under
this directory automatically, so a new design is validated by CI without touching
them.
