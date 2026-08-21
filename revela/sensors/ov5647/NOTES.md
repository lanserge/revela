# ov5647 — provenance of the reference profile

The reference sensor's fitted values, and where each number came
from. A profile without provenance is a superstition; these are the
receipts.

**Pedestal 16** (10-bit scale): measured 15 in the dark on this
module; 16 keeps a one-code guard.

**White balance (Q8.8: r 491, gr 256, gb 256, b 305)**: fitted from
the slope of a ColorChecker's neutral ramp in daylight — the RAMP,
not one grey patch, because red carries an offset that does not
scale with the light: infrared leaking past this module's weak
IR-cut filter. White balance and the colour matrix do different
jobs: the gains adapt to the illuminant (what an AWB loop does
continuously); the matrix corrects the sensor's filters and is
fixed.

**Colour matrix (Q.8, rows sum to 256)**: the PUBLISHED 5890 K
calibration for this sensor — deliberately not the one this bench
fitted. The bench fit came out with a 2.46× red diagonal because
the chart was measured through ~9% veiling glare, and on real
foliage — violently IR-bright through the weak cut filter — that
turned every leaf red. A lab calibration beats a contaminated one,
and the failure mode is invisible on a chart. (The contaminated fit
briefly lived on as a stale build artifact and reached a bench
screen twice; if colours ever look "good on the chart, red in the
garden", suspect this exact history.)

**Gamma**: 2.2, sampled as 33 knots at the build's bit depth.

**sensor.json**: the model-level facts, in the same schema-validated
format every sensor uses (provenance inside the file itself). The gain
law (code/16) and the integration ceiling (frame length minus 4) are
bench-verified — code 200 measured as 12.5×, and the clamp read live
off the running part. The apply delays are ecosystem-derived; promote
them to bench-verified when the AE work exercises the write scheduler.
