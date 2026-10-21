# revela.pipelines

Compositions of blocks, one to a file: the standard ISP, and designs built of
it. `revela/blocks/` holds the leaves -- algorithms, each with its own model;
everything composed of them is here.

```
revela/pipelines/
  bayer_isp.json   bayer_isp       the standard ISP for a Bayer sensor
  tof_core.json    tof_core        the standard ISP for a time-of-flight sensor
  tof_isp.json     tof_isp         tof_core behind a frame store
  stereo.json      revela_stereo   two eyes, each bayer_isp
```

A design holds a top, `revela_<file>`, referenced as
`revela.pipelines.<file>:revela_<file>`; the name is the generated top
module's, unique side by side and clear of anything in an integrator's own
Verilog. A standard ISP is named after its file -- `revela.pipelines.bayer_isp`
-- and is not a top: its stream parameters have no defaults, and whatever
instantiates it sets them.

## The standard ISPs

One for each kind of sensor. `revela.pipelines.bayer_isp` -- pedestal, white
balance, Hamilton-Adams demosaic, colour matrix; raw Bayer in, linear RGB
out. Every Bayer design is built of it rather than spelling a chain out
beside it: this folder's, and the board's
(`revela.boards.pynq_z2.isp:revela_isp_core`, which follows it with a display
curve and the mark). A tone curve and anything else a product adds belong to
the design, not to the ISP.

`revela.pipelines.tof_core` -- a time-of-flight sensor's phase frames in,
depth out: the four most recent reference phases to phase and amplitude
(`tofquad`, reading the stream's history), and phase to perpendicular depth
in millimetres (`tofrange`). Which phase a frame carries is its place in the
capture loop, bound to the frame count each header carries; the sensor's
phase order, range and optics are registers a sensor's tuning sets, and the
form of its samples -- two's complement below `headroom` zero bits -- is a
build parameter.

`revela.pipelines.tof_isp` -- `tof_core` behind np2hw's frame store, for a
sensor whose frames come in bursts and a display that takes them at its own
pace. The store keeps every frame, in external memory or block RAM, and
hands the core the newest four side by side as often as it asks; the core is
the `pipeline` parameter, `tof_core` or a design's own composite around it.
Of model blocks and a modelled leaf, it is proved as a design.

## What a design states, and what it does not

An ISP is sensor-independent. What it is built for is the stream entering it
-- the depth its samples arrive at, the widest frame and the tallest -- and
those three numbers are the ISP's parameters, stated on its input port. A
sensor whose mode fits them is served. No design names a sensor; a sensor's
tunings live with the sensor, in `revela/sensors/<name>/`, and name the block
they tune.

No design states an address either. Blocks declare local offsets and revela
assigns each instance a base at composition time; the schema has nowhere to
write one.

## A composite instantiated more than once

`stereo` instantiates the standard ISP twice, as `left` and `right`. Addresses
are allocated per INSTANCE -- `left.ccm` and `right.ccm` are independent
registers, reachable from the host as `dev.left.ccm` -- so each eye is tuned on
its own. What the sharing changes is the emitted RTL: one module per block,
instantiated per eye, instead of two identical copies.

## Building one

```bash
np2hw pipeline generate revela.pipelines.stereo:revela_stereo --out build

# the same build, read back to you -- where each block landed and the stream
# topology
python examples/build_pipeline.py revela.pipelines.stereo:revela_stereo
```

Both go through one emitter, which proves the RTL bit-exact against the models
before writing anything.

## Adding a design

Add `<name>.json` here holding the composite `revela_<name>`, built of the
standard ISP. The tests discover every design in this folder, so a new one is
built, generated and checked by CI without touching them.
