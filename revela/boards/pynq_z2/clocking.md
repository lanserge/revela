# Clocks and rates on the board

One problem, met twice on this board: whenever a pixel stream
crosses from one clock's authority into another's, the two sides
disagree about time, and something must absorb the disagreement.
This document records where each crossing lives, why it exists, and
how it is reconciled — because every one of these
mechanisms was paid for on a bench, and the reasons are easy to
lose.

## The principles

**A raster cannot wait.** A display signal has no pause button: the
pixel goes out on its clock edge, data or no data. Every mechanism
below exists downstream of this fact.

**Buffering decouples; it never reconciles.** A FIFO or a frame
store lets two clocks ignore each other for a while, but a rate
difference accumulates without bound. Whoever consumes must, on
average, exactly match whoever produces — or someone must drop or
repeat. The only question is where, and how visibly.

**Coherence kills drift, not inequality.** Two clocks derived from
one crystal hold a fixed ratio forever — but a fixed ratio is not a
ratio of exactly one. Frame periods are set by integers (a sensor's
VTS×HTS, a raster's htotal×vtotal, a PLL's dividers), each rounded
independently. The residual is a *constant* rate mismatch, which
slips phase at a constant speed and is indistinguishable, from the
inside, from crystal drift. Equality needs *co-designed integers*,
not just a shared crystal.

**The knob must be on something you own.** A receiver cannot slow a
cable down. Rate reconciliation always ends up at the point in the
chain where the adjustable degree of freedom lives.

**The coherence boundary is the cable.** Inside one box, feed every
clock from one family and choose the integers together; nothing
needs adjusting at run time. Across a cable there are two crystals,
and no amount of nominal agreement changes that.

## What arrives: the cable

The board's stream starts at the cable. Whatever clock crossing the
source has inside it -- for a Pi, its sensor onto its HDMI output -- is
the source's to reconcile, and the source keeps that story: picam2hdmi
slaves its HDMI output to its sensor
([GENLOCK.md](https://github.com/bayerlink/picam2hdmi/blob/main/GENLOCK.md)).
What reaches this board is one container frame per sensor frame, at the
source's cadence, on the source's clock -- the clock the TMDS link
recovers. The board has two clock domains of its own to keep apart from
it, and two crossings.

## Case 1 — into the board: cable → ISP

*Implemented as `link_reset` and np2hw's `stream_cdc` (`isp_cdc`), its
gray-pointer FIFO constrained by np2hw's own file, scoped to the FIFO;
wired in `pynq_demo.json`.*

**Why.** This crossing is not about rate at all. The clock
recovered from the TMDS link is the *sender's* clock, and it
**stops** — on unplug, and in the moments after reprogramming.
Anything that lives on it inherits that: an AXI slave clocked from
the cable once hung the processor until power cycle. So the rule is
structural: everything a host can reach, and everything that must
survive an unplug, lives on the board's own clocks; the recovered
clock's territory is the smallest possible — decode, unpack, and
one write port of a FIFO.

**How.** A dual-clock FIFO with gray-coded pointers crosses the
stream; the payload never crosses at all — it rests in memory until
the read side's own arithmetic says a slot is safely full. The
elastic is line-scale only: it absorbs jitter, and it is *not* a
rate adapter — a producer faster than its consumer needs a frame
store, not a deeper buffer. Loss is impossible to hide: the
receiver carries a sticky, observable overflow flag, because
unobservable loss is how demos lie. A supervisor on a clock that
cannot stop (`link_reset`) holds the cable-clocked domain in reset
while the link is down and releases it cleanly after lock returns.

The rejected alternative deserves its epitaph: adopting the
recovered clock as the display clock would genlock for free — and
take the display down with every unplug. The picture path is the
board's; the source is the cable's.

## Case 2 — into the display clock: how the frame rate passes through

*Implemented as np2hw's `scanout(genlock=True)` steering the display
MMCM's fine phase shifter; wired in `pynq_demo.json`.*

The output runs at the input's frame rate -- one frame out for every
frame in, with no frame buffer and a few lines of latency -- and this
crossing is how. The ISP runs on the board's FCLK0; the raster on the
board's own MMCM, because a raster cannot wait and the television must
keep its signal when the cable is out. So the picture crosses from one
clock into the other through a FIFO a few lines deep, and the raster's
clock is steered so that its frame rate equals the source's.

**Why.** Here the two-crystal reality finally bites: the sender's
frame rate (its crystal) versus the display raster (ours), measured
at −72 ppm on this bench. A frame store would bury the mismatch —
at the price of frames of latency, and of machinery to rotate and
supervise it. This design refuses that price: the picture path has
no store, ever, so the raster must consume at exactly the source's
average rate. The obvious knob — stretch vblank, as the Pi bridge
does — holds a receiver core happily and **loses a television**:
consumer panels re-run mode detection on any change of raster
geometry, however slow. One line per frame was refused; the same
raster frozen was solid.

**How.** The raster's geometry never changes. The following happens
in the clock itself: the display runs on its own MMCM, and the
genlock steps that MMCM's fine phase shifter ~24 ps at a time —
cumulative steps are a fractional frequency trim with roughly a
hundred ppm of authority, which is crystal territory, and crystals
are the whole job (an fps change is a mode switch, and software's).
Phase is measured where the stream itself provides it: the frame
start's arrival at the queue head, in pixels, against a target a
few lines ahead of the window — and that lead is *storage*, held in
the crossing FIFO. The steering debt is re-targeted at every
arrival and drained one PSDONE handshake at a time. An error past
two lines is not steerable at 24 ps: the counters jam to target
once, under a cooldown — the same price as a mode set. When the
source disappears, the steering stops and the raster keeps running
on its own; it never borrowed anything from the cable, so it has
nothing to give back.

Three configuration facts guard this design, each learned the hard
way: the MMCM's recipe is forced, not requested (a wizard's nearest
convenient frequency can exceed the entire steering authority);
per-output fine phase shift is explicitly enabled (the handshake
answers even when the output is deaf, which turns a genlock into
dead reckoning with a periodic snap); and the two on-board MMCMs
are declared asynchronous to the timing engine (the FIFO's gray
discipline is the synchronizer; a 6 ps setup demand across it helps
nobody).

## The case that needs nothing

A sensor attached directly to this board (a MIPI frontend) can take
its reference clock *from* the board: a bare sensor runs from the
MCLK it is given, though a camera module that carries its own
oscillator brings a clock of its own. Feed that reference from the
same family as the display clock and choose the sensor's VTS/HTS
together with the raster's htotal/vtotal so the frame periods are
exactly equal, and there is
nothing to adjust, ever — no bridge, no steering, no store. The
whole apparatus above is the price of a cable; on this side of one,
coherence plus co-designed integers makes the problem not exist.
