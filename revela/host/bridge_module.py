# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Export the revela ISP package for a picam2hdmi bridge.

Two packages share the bridge's module seam, split by what they are
allowed to touch. The SENSOR package is a driver: it writes its own
sensor's registers (integration, gains) and carries the sensor's
facts as data -- including the fitted ISP profile, as semantic
key/value pairs, never addresses. THIS package is the policy owner
and the only ISP-register writer: it owns the register map and the
cable's register channel, implements the bridge hooks, and calls
the sensor package through the driver ABI it publishes
(revela_sensor_api.h). Swap the sensor: one package changes. Swap
the ISP build: the other does.

The exporter bakes the register map into C at export time -- the
device parses nothing. A sensor package's profile keys are SCOPED,
``<composite>/<path relative to it>`` -- the sensor's tuning is for
bayer_isp, whichever design contains it -- and this package anchors
them at every instance of that composite the register map records, so
a sensor package survives any register relayout and any design.

The design's own values are already its bitstream's reset values. A
profile for the design may add settings the build does not carry
(--profile): resolved against the map here, baked in, and written at
link-up before the sensor's tuning.

    python -m revela.host.bridge_module \\
        --regmap revela_isp_core_regmap.json --out revela-isp.tar.gz
"""
import argparse
import io
import json
import sys
import tarfile
import time
from pathlib import Path

# The driver ABI's version, and the ONE place it is written. It was in
# five: a #define, two manifests and two banners, which agreed until the
# struct grew a field and four of them did not notice.
#   3  profile keys are scoped, <composite>/<relative path>, not
#      absolute block.param names.
SENSOR_API = 3

# The algorithm ABI's version, and the one place it is written. The
# manifest used to publish SENSOR_API under this name.
ALGO_API = 1

SENSOR_API_H = '''\
/* The driver ABI between the revela ISP package (policy, the only
 * ISP-register writer) and a sensor package (a driver: writes its
 * OWN sensor's registers, carries its facts and its fitted ISP
 * profile as DATA). Published by the ISP package; a sensor package
 * ships an identical copy, and the manifests' provides/requires
 * pair is checked before anything compiles. */
#ifndef REVELA_SENSOR_API_H
#define REVELA_SENSOR_API_H
#include <stdint.h>

#define REVELA_SENSOR_API {SENSOR_API}

/* fitted ISP parameters, keyed <composite>/<path relative to it>
 * ("revela.blocks.bayer_isp:bayer_isp/ccm.m_0_0") -- semantic keys,
 * never addresses, anchored by the ISP package wherever that
 * composite sits in its build; NULL key terminates */
struct revela_profile_entry {
    const char *key;
    int32_t value;
};

/* the module's lens, when it has one: the VCM is camera-module
 * hardware, so its facts and its actuator are the DRIVER's -- an
 * autofocus algorithm never knows whose lens it moves */
struct revela_focus_facts {
    uint32_t min, max, step;   /* actuator range, in its own units */
    uint32_t settle_ms;        /* worst move-to-still time */
};

/* The sensor's LAWS: what no ioctl can answer.
 *
 * A bridge can ask the sensor its geometry, its mosaic order, its
 * depth and its pixel rate, and it does. What it cannot ask is how a
 * gain CODE becomes a gain, how close to the frame length an exposure
 * may go, or how many frames pass before a write takes effect. Those
 * are facts about the part, they differ between parts, and an
 * exposure loop cannot be written without them -- so they travel with
 * the sensor, in the sensor's own package, beside its calibration.
 *
 * gain_unity_code is the code that means 1.0x, so gain = code /
 * unity: one law, parameterised, rather than a rule per part.
 * exposure_max_margin is how many lines must remain between the
 * integration time and the frame length. The apply delays are counted
 * in FRAMES, and a scheduler wants the DIFFERENCES between them. */
struct revela_sensor_facts {
    uint32_t gain_unity_code, gain_min_code, gain_max_code;
    uint32_t exposure_min_lines, exposure_max_margin;
    /* The longest integration that still fits a frame, from the
     * sensor's own timing: frame_length_lines - max_margin. A
     * starting point, not a policy -- AE owns it once AE exists. */
    uint32_t integration_default_lines;
    uint32_t delay_exposure, delay_analog_gain, delay_digital_gain;
    int32_t black_level;     /* the pedestal, at the sensor's depth */
};

struct revela_sensor_driver {
    int api;                 /* REVELA_SENSOR_API */
    const char *name;
    const struct revela_profile_entry *profile;
    /* actuators for the exposure cascade; fd is the camera subdev.
     * A calibration-only package may leave any of these NULL. */
    int (*set_integration_lines)(int fd, uint32_t lines);
    int (*set_analog_gain)(int fd, uint32_t code);
    int (*set_digital_gain)(int fd, uint32_t code); /* NULL: no Gd */
    const struct revela_sensor_facts *facts;  /* NULL: unstated     */
    const struct revela_focus_facts *focus;   /* NULL: fixed focus */
    int (*set_focus)(int fd, uint32_t position); /* NULL: no lens  */
};

/* implemented by the sensor package */
const struct revela_sensor_driver *revela_sensor(void);

#endif
'''

# The header is a plain string because it is full of C braces; the
# one number it must agree with is substituted here rather than
# written again inside it.
SENSOR_API_H = SENSOR_API_H.replace("{SENSOR_API}", str(SENSOR_API))


ALGO_API_H = '''\
/* The algorithm ABI: an algo package (autofocus and its kin) is
 * pure computation. It touches no hardware -- the ISP package
 * lends it these calls (semantic ISP access, the sensor driver's
 * actuators) and forwards it the bridge's frames. Absent, the
 * ISP package runs its baseline; present, it must match this
 * contract, checked at upload via provides/requires. */
#ifndef REVELA_ALGO_API_H
#define REVELA_ALGO_API_H
#include <stdint.h>
#include "revela_sensor_api.h"

#define REVELA_ALGO_API {ALGO_API}

struct bridge_frame;   /* ../../bridge.h */

struct revela_algo_ctx {
    int api;                    /* REVELA_ALGO_API */
    /* the driver, when installed -- lens and exposure actuators
     * live here, with the sensor's facts beside them */
    const struct revela_sensor_driver *sensor;
    /* semantic ISP access through the one writer: keys are
     * revela's block.param names; set is shadowed until commit */
    int (*isp_set)(const char *key, int32_t value);
    int (*isp_get)(const char *key, int32_t *value);
    int (*isp_commit)(void);
};

struct revela_algo {
    int api;                    /* REVELA_ALGO_API */
    const char *name;
    int  (*init)(const struct revela_algo_ctx *ctx);
    void (*on_frame)(const struct bridge_frame *f);
    void (*on_link_up)(void);
};

/* implemented by the algo package */
const struct revela_algo *revela_algo(void);

#endif
'''
ALGO_API_H = ALGO_API_H.replace("{ALGO_API}", str(ALGO_API))


def scoped_rows(regmap: dict) -> list[tuple[str, int, int]]:
    """Every register as each composite containing it names it:
    ``<composite ref>/<path relative to the instance>``. A composite
    instanced twice gives a key two rows, and a profile for it lands in
    both."""
    rows = []
    for c in regmap.get("composites", []):
        prefix = c["path"] + "." if c["path"] else ""
        for b in regmap["blocks"]:
            for r in b["registers"]:
                key = f"{b['path']}.{r['name']}"
                if key.startswith(prefix):
                    rows.append((f"{c['block']}/{key[len(prefix):]}",
                                 r["address"], r["bits"]))
    return rows


def generate_isp_c(regmap: dict, design: dict | None = None) -> str:
    """The ISP package's C. ``design`` is the design's own settings as
    absolute keys (a flattened profile), written at every link-up."""
    rows = []
    for b in regmap["blocks"]:
        for r in b["registers"]:
            rows.append((f"{b['path']}.{r['name']}", r["address"], r["bits"]))
    commit_addr = dict((k, a) for k, a, _ in rows)["pipe.commit"]
    where = {k: (a, bits) for k, a, bits in rows}
    design = design or {}
    unknown = sorted(set(design) - set(where))
    if unknown:
        raise ValueError(f"design values for registers this map does not "
                         f"have: {unknown}")

    L = []
    w = L.append
    w("/* generated by revela.host.bridge_module -- the ISP package:")
    w(" * the register map baked, the cable's register channel used,")
    w(" * the bridge hooks owned. The sensor package is a driver this")
    w(" * code calls; if none is installed, the hook says so and")
    w(" * touches nothing. */")
    w("#include <stdint.h>")
    w("#include <string.h>")
    w("#include <stdio.h>")
    w('#include "../../bridge.h"')
    w('#include "revela_sensor_api.h"')
    w('#include "revela_algo_api.h"')
    w("")
    w("/* the map: semantic key -> address and width. Its truth is the")
    w(" * receiver build's; the EDID gate below keeps a mismatched pair")
    w(" * from ever meeting. */")
    w("static const struct { const char *key; uint16_t reg; uint8_t bits; }")
    w("revela_map[] = {")
    for key, a, bits in sorted(rows, key=lambda x: x[1]):
        w(f'    {{ "{key}", 0x{a:04X}, {bits} }},')
    w("};")
    w(f"static const uint16_t revela_commit = 0x{commit_addr:04X};")
    w("")
    w("/* the same registers as each composite containing them names")
    w(" * them: where a sensor profile's scoped keys land */")
    w("static const struct { const char *key; uint16_t reg; uint8_t bits; }")
    w("revela_scoped[] = {")
    for key, a, bits in sorted(scoped_rows(regmap), key=lambda x: (x[1], x[0])):
        w(f'    {{ "{key}", 0x{a:04X}, {bits} }},')
    w('    { 0, 0, 0 },')
    w("};")
    w("")
    w("/* the design's own settings, resolved against this map at export */")
    w("static const struct { uint16_t reg; uint8_t bits; int32_t value; }")
    w("revela_design[] = {")
    for key, value in design.items():
        a, bits = where[key]
        w(f"    {{ 0x{a:04X}, {bits}, {value} }},  /* {key} */")
    w("    { 0, 0, 0 },")
    w("};")
    w("")
    w("/* every block's ID-and-version word, as this map expects it:")
    w(" * read at link-up, before anything is written */")
    w("static const struct { const char *path; uint16_t reg; uint32_t value; }")
    w("revela_ids[] = {")
    for b in regmap["blocks"]:
        iv = b["id_version"]
        w(f'    {{ "{b["path"]}", 0x{iv["address"]:04X}, 0x{iv["value"]:08X}u }},')
    w("    { 0, 0, 0 },")
    w("};")
    w("/* set only once the far end has proved it runs this build: no")
    w(" * writer -- link-up, panel, algo -- touches it before that */")
    w("static int map_ok;")
    w("")
    w("/* both companion packages are optional by construction: a")
    w(" * weak reference resolves to NULL when none is compiled in */")
    w("__attribute__((weak)) extern const struct revela_sensor_driver *")
    w("revela_sensor(void);")
    w("__attribute__((weak)) extern const struct revela_algo *")
    w("revela_algo(void);")
    w("")
    w("/* semantic ISP access lent to the algo package: the one")
    w(" * writer translates keys to registers; nobody else may */")
    w("static int isp_set(const char *key, int32_t value) {")
    w("    if (!map_ok) return -1;")
    w("    for (unsigned i = 0;")
    w("         i < sizeof revela_map / sizeof *revela_map; i++) {")
    w("        if (strcmp(revela_map[i].key, key)) continue;")
    w("        uint32_t mask = revela_map[i].bits >= 32 ? 0xFFFFFFFFu")
    w("            : ((1u << revela_map[i].bits) - 1u);")
    w("        return bridge_ddc_write(revela_map[i].reg,")
    w("                                ((uint32_t)value) & mask);")
    w("    }")
    w("    return -1;")
    w("}")
    w("static int isp_get(const char *key, int32_t *value) {")
    w("    if (!map_ok) return -1;")
    w("    for (unsigned i = 0;")
    w("         i < sizeof revela_map / sizeof *revela_map; i++) {")
    w("        if (strcmp(revela_map[i].key, key)) continue;")
    w("        uint32_t v;")
    w("        if (bridge_ddc_read(revela_map[i].reg, &v) < 0) return -1;")
    w("        *value = (int32_t)v;")
    w("        return 0;")
    w("    }")
    w("    return -1;")
    w("}")
    w("static int isp_commit(void) {")
    w("    if (!map_ok) return -1;")
    w("    return bridge_ddc_write(revela_commit, 1);")
    w("}")
    w("")
    w("static const struct revela_algo *algo;   /* after init only */")
    w("static struct revela_algo_ctx algo_ctx;")
    w("")
    w("void bridge_frame_hook(const struct bridge_frame *f) {")
    w("    /* STUB EXPOSURE, standing in until the real AE arrives as C")
    w("     * beside the stats block. It is not a loop and does not")
    w("     * measure anything: it applies the sensor's OWN declared")
    w("     * unity gain and its longest integration that fits a")
    w("     * frame, once, so a run starts from a defined point.")
    w("     *")
    w("     * A sensor whose gain nobody sets keeps whatever the last")
    w("     * caller left. On 2026-08-23 that was 12.5x -- the raw was")
    w("     * 55%% saturated before the ISP saw a pixel, and every")
    w("     * downstream stage was blamed in turn for a picture that")
    w("     * had already lost its highlights at the sensor.")
    w("     *")
    w("     * The value is the sensor package's, not this one's: the")
    w("     * gain law travels with the sensor, and unity is whatever")
    w("     * its own facts say it is. An algo package supersedes this")
    w("     * entirely -- if one is installed, exposure is its job.")
    w("     */")
    w("    static int gain_started = 0;")
    w("    if (!gain_started && !algo && f->sensor_fd >= 0 && revela_sensor) {")
    w("        const struct revela_sensor_driver *d = revela_sensor();")
    w("        if (d && d->facts) {")
    w("            uint32_t unity = d->facts->gain_unity_code;")
    w("            uint32_t lines = d->facts->integration_default_lines;")
    w("            int ok = 0;")
    w("            if (d->set_analog_gain &&")
    w("                d->set_analog_gain(f->sensor_fd, unity) == 0) ok |= 1;")
    w("            if (d->set_integration_lines && lines &&")
    w("                d->set_integration_lines(f->sensor_fd, lines) == 0)")
    w("                ok |= 2;")
    w("            if (ok) {")
    w('                printf("revela: %s: exposure started at unity gain '
      '(code %u) and %u lines -- stub until AE\\n",')
    w("                       d->name, unity, lines);")
    w("                gain_started = 1;")
    w("            }")
    w("        }")
    w("    }")
    w("    if (algo && algo->on_frame) algo->on_frame(f);")
    w("}")
    w("")
    w("void bridge_link_hook(const struct bridge_link *l) {")
    w("    map_ok = 0;")
    w("    if (!l->up || !l->edid) return;")
    w("    int ours = 0;")
    w("    for (int i = 0; i + 9 < 256; i++)")
    w('        if (!memcmp(l->edid + i, "BAYERLINK", 9)) { ours = 1; break; }')
    w("    if (!ours) {")
    w('        printf("revela: sink is not a bayerlink receiver; '
      'leaving it alone\\n");')
    w("        return;")
    w("    }")
    w("    /* THE BUILD CHECK. The EDID says the far end is a bayerlink")
    w("     * receiver; it does not say it is running the build this")
    w("     * map was cut from. Every block's ID-and-version word does,")
    w("     * so all of them are read before one register is written --")
    w("     * a different bitstream would otherwise take every value at")
    w("     * whatever now sits at these addresses, and say applied. */")
    w("    for (unsigned i = 0; revela_ids[i].path; i++) {")
    w("        uint32_t v;")
    w("        if (bridge_ddc_read(revela_ids[i].reg, &v) < 0) {")
    w('            printf("revela: the far end did not answer reading %s\'s '
      'ID; nothing written\\n", revela_ids[i].path);')
    w("            return;")
    w("        }")
    w("        if (v != revela_ids[i].value) {")
    w('            printf("revela: %s reads ID 0x%08X, this package was built '
      'for 0x%08X -- the bitstream is not the build this map came from; '
      'nothing written, re-export the package from the loaded build\\n",')
    w("                   revela_ids[i].path, (unsigned)v,")
    w("                   (unsigned)revela_ids[i].value);")
    w("            return;")
    w("        }")
    w("    }")
    w("    map_ok = 1;")
    w("")
    w("    int wrote = 0, unknown = 0, failed = 0;")
    w("    /* the design's settings first -- they are this package's, and")
    w("     * written whether or not a sensor package is installed --")
    w("     * then the sensor's tuning: a value the sensor measured wins */")
    w("    for (unsigned i = 0; revela_design[i].bits; i++) {")
    w("        uint32_t mask = revela_design[i].bits >= 32 ? 0xFFFFFFFFu")
    w("            : ((1u << revela_design[i].bits) - 1u);")
    w("        if (bridge_ddc_write(revela_design[i].reg,")
    w("                             ((uint32_t)revela_design[i].value) & mask) == 0)")
    w("            wrote++;")
    w("        else failed++;")
    w("    }")
    w("    const struct revela_sensor_driver *drv =")
    w("        revela_sensor ? revela_sensor() : 0;")
    w("    if (!revela_sensor)")
    w('        printf("revela: no sensor package installed -- the design\'s '
      'settings only\\n");')
    w("    else if (!drv || drv->api != REVELA_SENSOR_API || !drv->profile) {")
    w('        printf("revela: sensor package present but unusable '
      '(api %d, this package speaks %d)\\n", drv ? drv->api : -1, '
      'REVELA_SENSOR_API);')
    w("        drv = 0;")
    w("    }")
    w("    for (const struct revela_profile_entry *e = drv ? drv->profile : 0;")
    w("         e && e->key; e++) {")
    w("        int hit = 0;")
    w("        /* every row: a composite instanced twice takes it twice */")
    w("        for (unsigned i = 0; revela_scoped[i].key; i++) {")
    w("            if (strcmp(revela_scoped[i].key, e->key)) continue;")
    w("            hit = 1;")
    w("            uint32_t mask =")
    w("                revela_scoped[i].bits >= 32")
    w("                    ? 0xFFFFFFFFu")
    w("                    : ((1u << revela_scoped[i].bits) - 1u);")
    w("            uint32_t v = ((uint32_t)e->value) & mask;")
    w("            if (bridge_ddc_write(revela_scoped[i].reg, v) == 0)")
    w("                wrote++;")
    w("            else failed++;")
    w("        }")
    w("        if (!hit) unknown++;")
    w("    }")
    w("    bridge_ddc_write(revela_commit, 1);")
    w("    uint32_t c = 1;")
    w("    for (int t = 0; t < 100 && c; t++)")
    w("        if (bridge_ddc_read(revela_commit, &c) < 0) break;")
    w('    printf("revela: %s: %d registers restored over the cable'
      '%s%s, commit %s\\n",')
    w('           drv ? drv->name : "design only", wrote,')
    w('           failed ? " (some FAILED)" : "",')
    w('           unknown ? " (some keys unknown to this build)" : "",')
    w('           c ? "PENDING" : "applied");')
    w("    if (!drv) return;           /* an algo package needs a driver */")
    w("")
    w("    /* the algorithm package, if any, comes up AFTER the far")
    w("     * end is calibrated, with the driver and the semantic ISP")
    w("     * access in hand */")
    w("    if (revela_algo) {")
    w("        const struct revela_algo *a = revela_algo();")
    w("        if (a && a->api == REVELA_ALGO_API) {")
    w("            algo_ctx.api = REVELA_ALGO_API;")
    w("            algo_ctx.sensor = drv;")
    w("            algo_ctx.isp_set = isp_set;")
    w("            algo_ctx.isp_get = isp_get;")
    w("            algo_ctx.isp_commit = isp_commit;")
    w("            if (!algo) {")
    w("                if (a->init && a->init(&algo_ctx) == 0) algo = a;")
    w("                else if (!a->init) algo = a;")
    w('                if (algo) printf("revela: algo %s up\\n",')
    w("                                 algo->name);")
    w("            }")
    w("            if (algo && algo->on_link_up) algo->on_link_up();")
    w("        }")
    w("    }")
    w("}")
    w("")
    w("/* the panel's side of the same one-writer rule: a command line")
    w(" * from the bridge's control socket, translated through the same")
    w(" * key table every other writer uses. The panel renders its")
    w(" * widgets from the regmap.json riding this package, so what it")
    w(" * shows and what this table writes are one generated artifact. */")
    w("void bridge_command_hook(const char *cmd, char *reply, size_t cap) {")
    w("    char key[64];")
    w("    long long v;")
    w('    if (sscanf(cmd, "set %63s %lld", key, &v) == 2) {')
    w("        if (isp_set(key, (int32_t)v) == 0)")
    w('            snprintf(reply, cap, "ok");')
    w("        else")
    w('            snprintf(reply, cap, "err set %s: unknown key, the '
      'cable did not answer, or the far end is not the build this '
      'package was cut from", key);')
    w("        return;")
    w("    }")
    w('    if (sscanf(cmd, "get %63s", key) == 1) {')
    w("        int32_t value;")
    w("        if (isp_get(key, &value) == 0) {")
    w('            snprintf(reply, cap, "ok %ld", (long)value);')
    w("            return;")
    w("        }")
    w('        snprintf(reply, cap, "err get %s: unknown key, the cable '
      'did not answer, or the far end is not the build this package '
      'was cut from", key);')
    w("        return;")
    w("    }")
    w('    if (!strcmp(cmd, "commit")) {')
    w("        if (isp_commit() == 0)")
    w('            snprintf(reply, cap, "ok");')
    w("        else")
    w('            snprintf(reply, cap, "err commit: the cable did not '
      'answer, or the far end is not the build this package was cut '
      'from");')
    w("        return;")
    w("    }")
    w('    snprintf(reply, cap, "err unknown command; this module speaks: '
      'set <key> <raw>, get <key>, commit");')
    w("}")
    return "\n".join(L) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regmap", required=True)
    parser.add_argument("--profile", default=None,
                        help="the design's own settings: a profile for the "
                             "design (package.module:name), baked in")
    parser.add_argument("--library", action="append", metavar="DIR",
                        help="a directory to find libraries in, for this "
                             "run (see revela.designs.composite)")
    parser.add_argument("--version", default="0.1")
    parser.add_argument("--out", default="revela-isp.tar.gz")
    args = parser.parse_args()

    regmap = json.loads(Path(args.regmap).read_text())
    design = {}
    if args.profile:
        from revela.designs import profile
        from revela.designs.composite import add_libraries
        add_libraries(args.library)
        design = profile.flatten(profile.resolve(args.profile), regmap)
    files = {
        "manifest.json": json.dumps({
            "name": "revela-isp", "version": args.version,
            "kind": "picam2hdmi-module", "abi": 1,
            "slot": "isp",
            "provides": [f"revela-sensor-api:{SENSOR_API}",
                         f"revela-algo-api:{ALGO_API}"],
            "sources": ["revela_isp.c"],
        }, indent=2) + "\n",
        "revela_sensor_api.h": SENSOR_API_H,
        "revela_algo_api.h": ALGO_API_H,
        "revela_isp.c": generate_isp_c(regmap, design),
        # The map rides the package VERBATIM: the panel renders its
        # widgets from it (min/max/widget/choices are stated per
        # register), and because the C table above is generated from
        # the same map in the same breath, what the panel shows and
        # what the module writes cannot disagree.
        "regmap.json": json.dumps(regmap, indent=2) + "\n",
    }
    with tarfile.open(args.out, "w:gz") as tar:
        for fname, data in files.items():
            b = data.encode()
            info = tarfile.TarInfo(fname)
            info.size = len(b)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(b))
    print(f"{args.out}: revela-isp {args.version}, "
          f"map of {sum(len(b['registers']) for b in regmap['blocks'])} "
          f"registers baked, provides revela-sensor-api:{SENSOR_API}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
