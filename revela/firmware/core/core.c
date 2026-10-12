/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * core.c -- the firmware's core: the actions of revela_fw.h.
 *
 * The one writer of the ISP's registers and the display raster's. It
 * applies a configuration it is given -- the sensor's frame, the ISP's
 * window, the display raster, the 3A settings -- or refuses it before
 * writing anything, and runs whichever 3A modules are present once a
 * frame. Everything that varies between builds is in core_map.h, which
 * is generated from the build's register maps: this file is the same
 * for every build and every platform.
 */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "revela_algo.h"
#include "revela_fw.h"
#include "revela_hal.h"
#include "revela_sensor.h"
#include "core_map.h"

/* absent modules resolve to NULL */
__attribute__((weak)) const struct revela_sensor *revela_sensor(void);
__attribute__((weak)) const struct revela_algo *revela_ae(void);
__attribute__((weak)) const struct revela_algo *revela_awb(void);
__attribute__((weak)) const struct revela_algo *revela_af(void);

static const struct revela_hal *H;
static struct revela_config cur;
static int map_ok;                      /* the ISP proved it is this build */
static int configured;
static char last_err[160] = "not initialised";

/* how long a commit or an arm may take to be taken: a frame at the
 * slowest rate, and the bus's own time on top */
#define TAKE_POLLS 100
#define TAKE_POLL_US 2000

static void say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void say(const char *fmt, ...) {
    char line[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (H && H->log) H->log(line);
}

static int refuse(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int refuse(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(last_err, sizeof last_err, fmt, ap);
    va_end(ap);
    say("revela: %s", last_err);
    return -1;
}

static uint32_t masked(uint32_t value, uint8_t bits) {
    return bits >= 32 ? value : value & ((1u << bits) - 1u);
}

static int wr(uint32_t addr, uint32_t value) {
    return H->isp_write(addr, value);
}

/* write 1 to a register the hardware clears once it has taken what it
 * guards, and wait for it, a bounded while */
static int take(uint32_t addr) {
    if (wr(addr, 1) < 0) return -1;
    uint32_t v = 1;
    for (int i = 0; i < TAKE_POLLS && v; i++) {
        if (H->sleep_us) H->sleep_us(TAKE_POLL_US);
        if (H->isp_read(addr, &v) < 0) v = 1;
    }
    return v ? -1 : 0;
}

/* ---- named parameters ------------------------------------------- */

static const struct revela_reg *find(const char *key) {
    for (size_t i = 0; i < REVELA_MAP_N; i++)
        if (!strcmp(REVELA_MAP[i].key, key)) return &REVELA_MAP[i];
    return NULL;
}

int revela_fw_param_set(const char *key, int64_t value) {
    if (!map_ok) return refuse("set %s: the ISP has not proved its build", key);
    const struct revela_reg *r = find(key);
    if (!r) return refuse("set %s: no such parameter in this build", key);
    if (wr(r->addr, masked((uint32_t)value, r->bits)) < 0)
        return refuse("set %s: the ISP did not answer", key);
    return 0;
}

int revela_fw_param_get(const char *key, int64_t *value) {
    if (!map_ok) return refuse("get %s: the ISP has not proved its build", key);
    const struct revela_reg *r = find(key);
    if (!r) return refuse("get %s: no such parameter in this build", key);
    uint32_t v;
    if (H->isp_read(r->addr, &v) < 0)
        return refuse("get %s: the ISP did not answer", key);
    *value = v;
    return 0;
}

static int isp_set(const char *key, int32_t value) {
    return revela_fw_param_set(key, value);
}
static int isp_get(const char *key, int32_t *value) {
    int64_t v;
    int rc = revela_fw_param_get(key, &v);
    if (rc == 0) *value = (int32_t)v;
    return rc;
}
static int isp_commit(void) {
    return map_ok ? take(REVELA_COMMIT) : -1;
}

/* ---- the parts of a configuration ------------------------------- */

/* Everything a configuration can be refused for is checked here, before
 * a single register is written. */
static int check(const struct revela_config *c) {
    const struct revela_frame *f = &c->frame;
    const struct revela_raster *d = &c->display;
    const struct revela_window *w = &c->window;
    if (!f->width || !f->height || !f->bits)
        return refuse("the frame needs a width, a height and a depth");
    if (f->bayer_phase > 3)
        return refuse("bayer phase %u is not 0..3", (unsigned)f->bayer_phase);
    if (!(d->h_active < d->hs_beg && d->hs_beg < d->hs_end &&
          d->hs_end <= d->h_total && d->v_active < d->vs_beg &&
          d->vs_beg < d->vs_end && d->vs_end <= d->v_total && d->pixel_khz))
        return refuse("the display raster is not a raster: active, then "
                      "the sync, inside the totals, and a pixel clock");
    if (d->h_total >= (1u << REVELA_RASTER_XB) ||
        d->v_total >= (1u << REVELA_RASTER_YB))
        return refuse("a %ux%u raster is longer than the display's "
                      "counters (%u by %u bits)", (unsigned)d->h_total,
                      (unsigned)d->v_total, REVELA_RASTER_XB,
                      REVELA_RASTER_YB);
    uint32_t pw = w->width, ph = w->height;
    if (!w->center && (w->x0 + pw > d->h_active || w->y0 + ph > d->v_active))
        return refuse("the window %ux%u at %u,%u does not fit the %ux%u "
                      "display", (unsigned)pw, (unsigned)ph, (unsigned)w->x0,
                      (unsigned)w->y0, (unsigned)d->h_active,
                      (unsigned)d->v_active);
    if (pw > d->h_active || ph > d->v_active)
        return refuse("the window %ux%u is larger than the %ux%u display",
                      (unsigned)pw, (unsigned)ph, (unsigned)d->h_active,
                      (unsigned)d->v_active);
    return 0;
}

static int check_build(void) {
    for (size_t i = 0; REVELA_IDS[i].path; i++) {
        uint32_t v;
        if (H->isp_read(REVELA_IDS[i].addr, &v) < 0)
            return refuse("%s did not answer reading its ID",
                          REVELA_IDS[i].path);
        if (v != REVELA_IDS[i].value)
            return refuse("%s reads ID 0x%08X, this firmware was built for "
                          "0x%08X: the bitstream is not the build its map "
                          "came from", REVELA_IDS[i].path, (unsigned)v,
                          (unsigned)REVELA_IDS[i].value);
    }
    return 0;
}

/* The design's own settings, then the sensor's fitted profile wherever
 * its composite sits: a value the sensor measured wins. Not committed
 * here -- the ISP's window joins the same batch, and one commit carries
 * them all (while a commit is pending the ISP refuses further writes, so
 * two batches cannot be staged one behind the other). */
static int load_settings(void) {
    int wrote = 0, failed = 0, unknown = 0;
    for (size_t i = 0; REVELA_DESIGN[i].bits; i++) {
        if (wr(REVELA_DESIGN[i].addr,
               masked((uint32_t)REVELA_DESIGN[i].value,
                      REVELA_DESIGN[i].bits)) == 0) wrote++;
        else failed++;
    }
    const struct revela_sensor *s = revela_sensor ? revela_sensor() : NULL;
    if (s && (s->api != REVELA_SENSOR_API || !s->profile)) {
        say("revela: the sensor module speaks api %d, this firmware %d; "
            "its profile is not loaded", s->api, REVELA_SENSOR_API);
        s = NULL;
    }
    for (const struct revela_profile_entry *e = s ? s->profile : NULL;
         e && e->key; e++) {
        int hit = 0;
        for (size_t i = 0; REVELA_SCOPED[i].key; i++) {
            if (strcmp(REVELA_SCOPED[i].key, e->key)) continue;
            hit = 1;
            if (wr(REVELA_SCOPED[i].addr,
                   masked((uint32_t)e->value, REVELA_SCOPED[i].bits)) == 0)
                wrote++;
            else failed++;
        }
        if (!hit) unknown++;
    }
    say("revela: %s: %d settings written%s%s", s ? s->name : "design only",
        wrote, failed ? " (some FAILED)" : "",
        unknown ? " (some keys unknown to this build)" : "");
    return failed ? -1 : 0;
}

/* A commit still pending means the ISP refuses writes -- and it may never
 * be taken: the ISP takes a commit at the end of a frame of its CURRENT
 * window, and after the sensor's mode has changed, frames of the old size
 * may never come. The commit word stays writable for exactly this: a new
 * configuration releases the untaken batch (its values stay in the
 * registers) and commits the whole again, the new window with it. */
static void release(void) {
    uint32_t v = 0;
    if (H->isp_read(REVELA_COMMIT, &v) == 0 && v) {
        wr(REVELA_COMMIT, 0);
        say("revela: an untaken batch released; this one carries it");
    }
}

/* The ISP's window -- the whole frame, as much of it as the build holds
 * -- and the batch it joins, committed together. A commit still pending
 * afterwards is not a failure: the datapath takes the batch at a frame
 * boundary, and while a mode changes or a link comes up there may be no
 * frame yet; the values are in the registers and land with the first. */
static int apply_isp(const struct revela_config *c) {
    uint32_t w = c->frame.width < REVELA_ISP_WIDTH ? c->frame.width
                                                   : REVELA_ISP_WIDTH;
    int bad = 0;
    bad |= wr(REVELA_WIN_X0, 0) < 0;
    bad |= wr(REVELA_WIN_Y0, 0) < 0;
    bad |= wr(REVELA_WIN_X1, w) < 0;
    bad |= wr(REVELA_WIN_Y1, c->frame.height) < 0;
#ifdef REVELA_BAYER_PHASE
    bad |= wr(REVELA_BAYER_PHASE, c->frame.bayer_phase) < 0;
#endif
    if (bad) return refuse("the ISP did not take its window");
    say("revela: the ISP's settings and window committed, %s",
        take(REVELA_COMMIT) == 0 ? "applied"
                                 : "pending, until the ISP's next frame");
    return 0;
}

/* The display raster and where the picture sits in it, armed together
 * and taken by the display in its next vertical blanking. */
static int apply_display(const struct revela_config *c) {
    const struct revela_raster *d = &c->display;
    const struct revela_window *w = &c->window;
    uint32_t pw = w->width, ph = w->height;
    if (!pw) pw = c->frame.width < d->h_active ? c->frame.width : d->h_active;
    if (!ph) ph = c->frame.height < d->v_active ? c->frame.height : d->v_active;
    const uint32_t v[][2] = {
        {REVELA_R_H_TOTAL, d->h_total}, {REVELA_R_H_ACTIVE, d->h_active},
        {REVELA_R_HS_BEG, d->hs_beg},   {REVELA_R_HS_END, d->hs_end},
        {REVELA_R_V_TOTAL, d->v_total}, {REVELA_R_V_ACTIVE, d->v_active},
        {REVELA_R_VS_BEG, d->vs_beg},   {REVELA_R_VS_END, d->vs_end},
        {REVELA_R_HS_POS, d->hsync_positive},
        {REVELA_R_VS_POS, d->vsync_positive},
        {REVELA_R_WIN_X0, w->x0}, {REVELA_R_WIN_Y0, w->y0},
        {REVELA_R_WIN_W, pw}, {REVELA_R_WIN_H, ph},
        {REVELA_R_CENTER, w->center},
    };
    for (size_t i = 0; i < sizeof v / sizeof v[0]; i++)
        if (wr(v[i][0], v[i][1]) < 0)
            return refuse("the display did not answer; not armed");
    if (take(REVELA_R_ARM) < 0)
        return refuse("the display did not take its raster (still armed)");
    return 0;
}

static int apply_sensor(const struct revela_config *c) {
    if (!H->sensor_mode) return 0;      /* the platform sets it elsewhere */
    const struct revela_frame *f = &c->frame;
    if (H->sensor_mode(f->width, f->height, f->bits, f->line_ns,
                       f->frame_lines) < 0)
        return refuse("the sensor did not take %ux%u, %u-bit, %u ns lines, "
                      "%u-line frames", (unsigned)f->width,
                      (unsigned)f->height, (unsigned)f->bits,
                      (unsigned)f->line_ns, (unsigned)f->frame_lines);
    return 0;
}

/* ---- 3A ----------------------------------------------------------- */

struct loop {
    const char *name;
    const struct revela_algo *(*get)(void);
    const struct revela_algo *up;       /* after its init */
};
static struct loop loops[3];

static void loops_up(void) {
    loops[0] = (struct loop){"ae", revela_ae, NULL};
    loops[1] = (struct loop){"awb", revela_awb, NULL};
    loops[2] = (struct loop){"af", revela_af, NULL};
    const struct revela_sensor *s = revela_sensor ? revela_sensor() : NULL;
    static struct revela_algo_env env;
    env = (struct revela_algo_env){
        .hal = H, .sensor = s ? s->facts : NULL,
        .isp_set = isp_set, .isp_get = isp_get, .isp_commit = isp_commit};
    for (int i = 0; i < 3; i++) {
        const struct revela_algo *a = loops[i].get ? loops[i].get() : NULL;
        if (!a) continue;
        if (a->api != REVELA_ALGO_API) {
            say("revela: the %s module speaks api %d, this firmware %d; "
                "not run", loops[i].name, a->api, REVELA_ALGO_API);
            continue;
        }
        if (a->init && a->init(&env) < 0) {
            say("revela: the %s module (%s) did not come up", loops[i].name,
                a->name);
            continue;
        }
        loops[i].up = a;
        say("revela: %s: %s up", loops[i].name, a->name);
    }
}

static void loops_configure(const struct revela_config *c) {
    for (int i = 0; i < 3; i++)
        if (loops[i].up && loops[i].up->configure)
            loops[i].up->configure(&c->aaa, &c->frame);
}

static int loop_on(int i) {
    return i == 0 ? cur.aaa.ae : i == 1 ? cur.aaa.awb : cur.aaa.af;
}

void revela_fw_tick(const struct revela_tick *t) {
    if (!configured) return;
    for (int i = 0; i < 3; i++)
        if (loops[i].up && loops[i].up->tick && loop_on(i))
            loops[i].up->tick(t);
}

/* ---- the actions ------------------------------------------------ */

void revela_fw_attach(const struct revela_hal *hal) { H = hal; }

static int apply(const struct revela_config *c, int sensor_changed) {
    if (sensor_changed && apply_sensor(c) < 0) return -1;
    if (apply_isp(c) < 0) return -1;
    if (apply_display(c) < 0) return -1;
    cur = *c;
    configured = 1;
    loops_configure(c);
    last_err[0] = 0;
    return 0;
}

int revela_fw_init(const struct revela_config *c) {
    if (!H) return -1;
    map_ok = configured = 0;
    if (check(c) < 0) return -1;
    if (check_build() < 0) return -1;
    map_ok = 1;
    release();
    if (load_settings() < 0) return refuse("the ISP's settings did not load");
    loops_up();
    return apply(c, 1);
}

int revela_fw_configure(const struct revela_config *c) {
    if (!H || !map_ok)
        return refuse("configure before init: nothing is proven yet");
    if (check(c) < 0) return -1;
    release();
    int sensor_changed = memcmp(&c->frame, &cur.frame, sizeof c->frame) != 0;
    return apply(c, sensor_changed);
}

int revela_fw_status(char *line, uint32_t cap) {
    const struct revela_frame *f = &cur.frame;
    const struct revela_raster *d = &cur.display;
    int n = snprintf(line, cap, "ok build=%s frame=%ux%ux%u display=%ux%u/"
                     "%ukHz", map_ok ? "proven" : "unproven",
                     (unsigned)f->width, (unsigned)f->height,
                     (unsigned)f->bits, (unsigned)d->h_active,
                     (unsigned)d->v_active, (unsigned)d->pixel_khz);
    for (int i = 0; i < 3 && n > 0 && (uint32_t)n < cap; i++)
        n += snprintf(line + n, cap - n, " %s=%s", loops[i].name ?
                      loops[i].name : (i == 0 ? "ae" : i == 1 ? "awb" : "af"),
                      !loops[i].up ? "absent" : loop_on(i) ? "on" : "off");
    if (n > 0 && (uint32_t)n < cap && last_err[0])
        snprintf(line + n, cap - n, " last=\"%s\"", last_err);
    return 0;
}

/* ---- the text console ------------------------------------------- */

#define FIELD(s, m, t) {#s "." #m, offsetof(struct revela_config, s) + \
                        offsetof(t, m), sizeof(((t *)0)->m)}
static const struct { const char *key; size_t at, size; } FIELDS[] = {
    FIELD(frame, width, struct revela_frame),
    FIELD(frame, height, struct revela_frame),
    FIELD(frame, bits, struct revela_frame),
    FIELD(frame, bayer_phase, struct revela_frame),
    FIELD(frame, line_ns, struct revela_frame),
    FIELD(frame, frame_lines, struct revela_frame),
    FIELD(display, pixel_khz, struct revela_raster),
    FIELD(display, h_total, struct revela_raster),
    FIELD(display, v_total, struct revela_raster),
    FIELD(display, h_active, struct revela_raster),
    FIELD(display, v_active, struct revela_raster),
    FIELD(display, hs_beg, struct revela_raster),
    FIELD(display, hs_end, struct revela_raster),
    FIELD(display, vs_beg, struct revela_raster),
    FIELD(display, vs_end, struct revela_raster),
    FIELD(display, hsync_positive, struct revela_raster),
    FIELD(display, vsync_positive, struct revela_raster),
    FIELD(window, x0, struct revela_window),
    FIELD(window, y0, struct revela_window),
    FIELD(window, width, struct revela_window),
    FIELD(window, height, struct revela_window),
    FIELD(window, center, struct revela_window),
    FIELD(aaa, ae, struct revela_3a),
    FIELD(aaa, awb, struct revela_3a),
    FIELD(aaa, af, struct revela_3a),
    FIELD(aaa, ae_target, struct revela_3a),
    FIELD(aaa, exposure_max, struct revela_3a),
    FIELD(aaa, gain_max, struct revela_3a),
};

/* key=value pairs onto a copy of the current configuration */
static int parse(const char *text, struct revela_config *c, char *err,
                 uint32_t cap) {
    *c = cur;
    char buf[2048];
    snprintf(buf, sizeof buf, "%s", text);
    for (char *tok = strtok(buf, " \t"); tok; tok = strtok(NULL, " \t")) {
        char *eq = strchr(tok, '=');
        if (!eq) { snprintf(err, cap, "err %s: not key=value", tok); return -1; }
        *eq = 0;
        size_t i = 0;
        while (i < sizeof FIELDS / sizeof FIELDS[0] && strcmp(FIELDS[i].key, tok))
            i++;
        if (i == sizeof FIELDS / sizeof FIELDS[0]) {
            snprintf(err, cap, "err %s: no such field", tok);
            return -1;
        }
        char *end;
        unsigned long v = strtoul(eq + 1, &end, 0);
        if (*end) { snprintf(err, cap, "err %s: not a number", tok); return -1; }
        uint8_t *p = (uint8_t *)c + FIELDS[i].at;
        if (FIELDS[i].size == 1) *p = (uint8_t)v;
        else { uint32_t u = (uint32_t)v; memcpy(p, &u, sizeof u); }
    }
    return 0;
}

int revela_fw_command(const char *line, char *reply, uint32_t cap) {
    struct revela_config c;
    char key[96];
    long long v;
    int init = !strncmp(line, "init", 4) && (!line[4] || line[4] == ' ');
    int conf = !strncmp(line, "configure", 9) && (!line[9] || line[9] == ' ');
    if (init || conf) {
        if (parse(line + (init ? 4 : 9), &c, reply, cap) < 0) return -1;
        int rc = init ? revela_fw_init(&c) : revela_fw_configure(&c);
        if (rc == 0) snprintf(reply, cap, "ok");
        else snprintf(reply, cap, "err %s", last_err);
        return rc;
    }
    if (sscanf(line, "set %95s %lld", key, &v) == 2) {
        int rc = revela_fw_param_set(key, v);
        if (rc == 0) snprintf(reply, cap, "ok");
        else snprintf(reply, cap, "err %s", last_err);
        return rc;
    }
    if (sscanf(line, "get %95s", key) == 1) {
        int64_t got;
        int rc = revela_fw_param_get(key, &got);
        if (rc == 0) snprintf(reply, cap, "ok %lld", (long long)got);
        else snprintf(reply, cap, "err %s", last_err);
        return rc;
    }
    if (!strcmp(line, "status")) return revela_fw_status(reply, cap);
    if (!strcmp(line, "config")) {
        /* the current configuration as init would take it back */
        int n = snprintf(reply, cap, "ok");
        for (size_t i = 0; i < sizeof FIELDS / sizeof FIELDS[0] && n > 0 &&
             (uint32_t)n < cap; i++) {
            const uint8_t *p = (const uint8_t *)&cur + FIELDS[i].at;
            uint32_t u = 0;
            if (FIELDS[i].size == 1) u = *p;
            else memcpy(&u, p, sizeof u);
            n += snprintf(reply + n, cap - n, " %s=%u", FIELDS[i].key,
                          (unsigned)u);
        }
        return 0;
    }
    snprintf(reply, cap, "err unknown command; the firmware speaks: init "
             "<field>=<value>..., configure <field>=<value>..., set <param> "
             "<value>, get <param>, status, config");
    return -1;
}
