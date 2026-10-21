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
    /* the display's own file, raster.<field>: a bench's way to read or
     * nudge any of its fields (a change takes with raster.arm) */
    for (size_t i = 0; i < REVELA_RASTER_MAP_N; i++)
        if (!strcmp(REVELA_RASTER_MAP[i].key, key))
            return &REVELA_RASTER_MAP[i];
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

/* Which row of the display's clock table makes `khz`, or -1. */
static int clock_row(uint32_t khz) {
    for (uint32_t i = 0; i < REVELA_N_CLOCKS; i++)
        if (REVELA_CLOCKS[i].khz == khz) return (int)i;
    return -1;
}

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
    if (clock_row(d->pixel_khz) < 0)
        return refuse("the display makes no %u kHz clock: see its clocks "
                      "in the status", (unsigned)d->pixel_khz);
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

/* Commit what is written, and say whether the ISP has taken it. Pending
 * is not a failure: the datapath takes a batch at a frame boundary, and
 * while a mode changes or a link comes up there may be no frame yet; the
 * values are in the registers and land with the first. */
static void commit_batch(const char *what) {
    say("revela: %s committed, %s", what,
        take(REVELA_COMMIT) == 0 ? "applied"
                                 : "pending, until the ISP's next frame");
}

/* The picture: as much of the frame as the ISP holds, and of that the
 * window's size where one is stated, else as much as the display's active
 * area shows. */
static void picture(const struct revela_config *c, uint32_t *fw, uint32_t *fh,
                    uint32_t *pw, uint32_t *ph) {
    *fw = c->frame.width < REVELA_ISP_WIDTH ? c->frame.width : REVELA_ISP_WIDTH;
    *fh = c->frame.height < REVELA_ISP_HEIGHT ? c->frame.height
                                              : REVELA_ISP_HEIGHT;
    *pw = c->window.width ? c->window.width
        : *fw < c->display.h_active ? *fw : c->display.h_active;
    *ph = c->window.height ? c->window.height
        : *fh < c->display.v_active ? *fh : c->display.v_active;
    if (*pw > *fw) *pw = *fw;
    if (*ph > *fh) *ph = *fh;
}

/* The ISP follows each frame's own header: its geometry, its depth, and
 * -- where the build carries the phase in the stream -- its Bayer phase.
 * A build that takes the phase as a register gets it here. So does the
 * picture: the crop keeps the middle of the frame, the picture's size,
 * and everything after it, the display included, is sized by what it
 * keeps; and the mark goes in that picture's corner. One batch carries
 * them all. */
static int apply_isp(const struct revela_config *c) {
    int batch = 0;
    int changed = !configured
        || c->frame.width != cur.frame.width
        || c->frame.height != cur.frame.height
        || c->frame.bayer_phase != cur.frame.bayer_phase
        || c->display.h_active != cur.display.h_active
        || c->display.v_active != cur.display.v_active
        || c->window.width != cur.window.width
        || c->window.height != cur.window.height;
    if (!changed) return 0;
    /* an untaken batch -- a link still coming up, a mode the ISP has no
     * frame of yet -- refuses these writes: release it, and this batch
     * carries its values with the new ones */
    release();
#ifdef REVELA_BAYER_PHASE
    if (wr(REVELA_BAYER_PHASE, c->frame.bayer_phase) < 0)
        return refuse("the ISP did not take its Bayer phase");
    batch = 1;
#endif
    uint32_t fw, fh, w, h;
    picture(c, &fw, &fh, &w, &h);
#ifdef REVELA_CROP_X0
    if (wr(REVELA_CROP_X0, (fw - w) / 2) < 0
        || wr(REVELA_CROP_Y0, (fh - h) / 2) < 0
        || wr(REVELA_CROP_W, w) < 0 || wr(REVELA_CROP_H, h) < 0)
        return refuse("the ISP did not take its crop");
    batch = 1;
#else
    /* no crop: the picture is the frame */
    w = fw; h = fh;
#endif
#ifdef REVELA_LOGO_X0
    /* a picture too small for the mark gets none: placed past its end */
    uint32_t x0 = w > REVELA_LOGO_INSET ? w - REVELA_LOGO_INSET : w;
    uint32_t y0 = h > REVELA_LOGO_INSET ? h - REVELA_LOGO_INSET : h;
    if (wr(REVELA_LOGO_X0, x0) < 0 || wr(REVELA_LOGO_Y0, y0) < 0)
        return refuse("the ISP did not take the mark's place");
    batch = 1;
#endif
    if (batch) commit_batch("the frame's settings");
    return 0;
}

#ifdef REVELA_PLL_BASE
/* The serializer MMCM's recipe for a clock row, in two halves around the
 * clock select's switch. The wizard reconfigures the MMCM BY ITSELF the
 * moment it loses lock, with whatever recipe its registers then hold, and
 * takes no load at all until it locks again -- so the new recipe is in
 * its registers BEFORE the clock moves: a switch that costs the lock
 * relocks on the new recipe, not the old one, which may never lock at the
 * new clock. Its divider and multiplier, its x5 output's divider, and
 * SADDR (use these registers, not the build's defaults), without load.
 * The configuration writes are made twice: the register file latches
 * each write's value into its recipe on the NEXT write to the same
 * address. */
static int pll_stage(int row) {
    const uint32_t base = REVELA_PLL_BASE;
    uint32_t fb = REVELA_CLOCKS[row].divclk | (REVELA_CLOCKS[row].mult << 8);
    for (int i = 0; i < 2; i++) {
        if (wr(base + 0x200, fb) < 0) return -1;
        if (wr(base + 0x208, REVELA_CLOCKS[row].out0) < 0) return -1;
    }
    return wr(base + 0x25C, 0x2);
}

/* ...and after the switch, load -- for a switch that kept the lock, which
 * started nothing -- and wait for the lock at five times the new clock. */
static int pll_load(void) {
    const uint32_t base = REVELA_PLL_BASE;
    if (wr(base + 0x25C, 0x3) < 0) return -1;
    uint32_t st = 0;
    for (int i = 0; i < TAKE_POLLS && !(st & 1); i++) {
        if (H->sleep_us) H->sleep_us(TAKE_POLL_US);
        if (H->isp_read(base + 0x004, &st) < 0) st = 0;
    }
    return (st & 1) ? 0 : -1;
}

/* What the serializer MMCM holds, for a refusal to say: its recipe
 * registers as read back, and its status. */
static void pll_readback(uint32_t *fb, uint32_t *out0, uint32_t *st) {
    const uint32_t base = REVELA_PLL_BASE;
    if (H->isp_read(base + 0x200, fb) < 0) *fb = 0xFFFFFFFFu;
    if (H->isp_read(base + 0x208, out0) < 0) *out0 = 0xFFFFFFFFu;
    if (H->isp_read(base + 0x004, st) < 0) *st = 0xFFFFFFFFu;
}
#endif

/* Whether the display already runs at clock row `row`: its select on that
 * row, and -- where the build carries the serializer's MMCM -- that MMCM
 * locked on that row's recipe, as its registers read back. */
static int clock_held(int row) {
    uint32_t sel = 0xFFFFFFFFu;
    if (H->isp_read(REVELA_R_CLK_SEL, &sel) < 0 || sel != (uint32_t)row)
        return 0;
#ifdef REVELA_PLL_BASE
    uint32_t fb, out0, st;
    pll_readback(&fb, &out0, &st);
    return (st & 1)
        && fb == (REVELA_CLOCKS[row].divclk | (REVELA_CLOCKS[row].mult << 8))
        && out0 == REVELA_CLOCKS[row].out0;
#else
    return 1;
#endif
}

/* The display raster and where the picture sits in it, armed together
 * and taken by the display in its next vertical blanking. */
static int apply_display(const struct revela_config *c) {
    const struct revela_raster *d = &c->display;
    const struct revela_window *w = &c->window;
    /* the picture's size is not the display's to be told: it is the
     * ISP's output, and the display takes it from the stream */
    /* the clock's row is one of the raster's numbers: the display takes
     * them all together, after its frame's last line, so the clock
     * switches between frames with the mode it is for */
    int row = clock_row(d->pixel_khz);
    const uint32_t v[][2] = {
        {REVELA_R_CLK_SEL, (uint32_t)row},
        {REVELA_R_PS_PER_PX, REVELA_CLOCKS[row].ps_per_px},
        {REVELA_R_H_TOTAL, d->h_total}, {REVELA_R_H_ACTIVE, d->h_active},
        {REVELA_R_HS_BEG, d->hs_beg},   {REVELA_R_HS_END, d->hs_end},
        {REVELA_R_V_TOTAL, d->v_total}, {REVELA_R_V_ACTIVE, d->v_active},
        {REVELA_R_VS_BEG, d->vs_beg},   {REVELA_R_VS_END, d->vs_end},
        {REVELA_R_HS_POS, d->hsync_positive},
        {REVELA_R_VS_POS, d->vsync_positive},
        {REVELA_R_WIN_X0, w->x0}, {REVELA_R_WIN_Y0, w->y0},
        {REVELA_R_CENTER, w->center},
    };
    /* the serializer's new recipe, staged; the raster's numbers, the
     * clock's row among them, armed and taken -- the clock switches as
     * the display takes them; then the serializer loaded on the new clock.
     * A clock the hardware already runs is left alone -- reloading the
     * serializer stops the HDMI clock for a moment, and a TV takes seconds
     * to find the signal again. The hardware is asked, not this copy's
     * memory: the firmware starts fresh with every run of its platform,
     * and the board keeps its clock through all of them. */
    int switching = !clock_held(row);
#ifdef REVELA_PLL_BASE
    if (switching && pll_stage(row) < 0)
        return refuse("the serializer MMCM did not answer; not armed");
#endif
    for (size_t i = 0; i < sizeof v / sizeof v[0]; i++)
        if (wr(v[i][0], v[i][1]) < 0)
            return refuse("the display did not answer; not armed");
    if (take(REVELA_R_ARM) < 0)
        return refuse("the display did not take its raster (still armed)");
#ifdef REVELA_PLL_BASE
    if (switching && pll_load() < 0) {
        uint32_t fb, out0, st;
        pll_readback(&fb, &out0, &st);
        return refuse("the serializer MMCM did not lock at five times "
                      "%u kHz (reads 0x200=%08x 0x208=%08x status=%08x)",
                      (unsigned)REVELA_CLOCKS[row].khz, (unsigned)fb,
                      (unsigned)out0, (unsigned)st);
    }
#endif
    return 0;
}

/* The sensor module, where one is loaded and speaks this firmware's api. */
static const struct revela_sensor *sensor(void) {
    const struct revela_sensor *s = revela_sensor ? revela_sensor() : NULL;
    return (s && s->api == REVELA_SENSOR_API) ? s : NULL;
}

/* A mode of the sensor module's: by name, or -- a frame stated by size --
 * the one of that size and depth. NULL where none is. */
static const struct revela_sensor_mode *find_mode(const struct revela_frame *f) {
    const struct revela_sensor *s = sensor();
    for (uint32_t i = 0; s && i < s->n_modes; i++) {
        const struct revela_sensor_mode *m = &s->modes[i];
        if (f->mode[0] ? !strcmp(m->name, f->mode)
                       : (m->width == f->width && m->height == f->height
                          && m->bits == f->bits))
            return m;
    }
    return NULL;
}

/* Select the frame's mode on the sensor: a platform with a driver of its
 * own is told the mode to hold; one that only moves bytes has its
 * registers written, the mode's writes in order. */
static int apply_sensor(const struct revela_config *c) {
    if (!H->sensor_mode && !H->sensor_write) return 0;  /* set elsewhere */
    const struct revela_sensor_mode *m = find_mode(&c->frame);
    if (!m)
        return refuse("the sensor module has no %s%s%ux%u %u-bit mode",
                      c->frame.mode[0] ? c->frame.mode : "",
                      c->frame.mode[0] ? ", nor a " : "",
                      (unsigned)c->frame.width, (unsigned)c->frame.height,
                      (unsigned)c->frame.bits);
    if (H->sensor_mode) {
        if (H->sensor_mode(m) < 0)
            return refuse("the sensor did not take mode %s", m->name);
        return 0;
    }
    for (uint32_t i = 0; i < m->n_writes; i++)
        if (H->sensor_write(m->writes[i].reg, m->writes[i].value) < 0)
            return refuse("the sensor did not take mode %s (register "
                          "0x%04x)", m->name, (unsigned)m->writes[i].reg);
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
    commit_batch("the ISP's settings");
    loops_up();
    return apply(c, 1);
}

int revela_fw_configure(const struct revela_config *c) {
    if (!H || !map_ok)
        return refuse("configure before init: nothing is proven yet");
    if (check(c) < 0) return -1;
    int sensor_changed = memcmp(&c->frame, &cur.frame, sizeof c->frame) != 0;
    return apply(c, sensor_changed);
}

int revela_fw_status(char *line, uint32_t cap) {
    const struct revela_frame *f = &cur.frame;
    const struct revela_raster *d = &cur.display;
    int n = snprintf(line, cap, "ok build=%s tv_khz=", map_ok ? "proven"
                                                          : "unproven");
    for (uint32_t i = 0; i < REVELA_N_CLOCKS && n > 0 && (uint32_t)n < cap;
         i++)
        n += snprintf(line + n, cap - n, i ? ",%u" : "%u",
                      (unsigned)REVELA_CLOCKS[i].khz);
    /* the clock the ISP takes a sample a cycle on: no source may hand it
     * a line faster than that */
    if (n > 0 && (uint32_t)n < cap)
        n += snprintf(line + n, cap - n, " isp_khz=%u", (unsigned)REVELA_ISP_KHZ);
    /* the display's counters, in bits: the longest raster it scans */
    if (n > 0 && (uint32_t)n < cap)
        n += snprintf(line + n, cap - n, " counters=%ux%u",
                      (unsigned)REVELA_RASTER_XB, (unsigned)REVELA_RASTER_YB);
    if (n > 0 && (uint32_t)n < cap)
        n += snprintf(line + n, cap - n, " frame=%ux%ux%u display=%ux%u/%ukHz",
                     (unsigned)f->width, (unsigned)f->height,
                     (unsigned)f->bits, (unsigned)d->h_active,
                     (unsigned)d->v_active, (unsigned)d->pixel_khz);
    for (int i = 0; i < 3 && n > 0 && (uint32_t)n < cap; i++)
        n += snprintf(line + n, cap - n, " %s=%s", loops[i].name ?
                      loops[i].name : (i == 0 ? "ae" : i == 1 ? "awb" : "af"),
                      !loops[i].up ? "absent" : loop_on(i) ? "on" : "off");
#ifdef REVELA_PLL_BASE
    /* the serializer's clock, as it is now: locked, or not */
    uint32_t st;
    if (n > 0 && (uint32_t)n < cap)
        n += snprintf(line + n, cap - n, " tx_lock=%s",
                      H->isp_read(REVELA_PLL_BASE + 0x004, &st) < 0 ? "?"
                      : (st & 1) ? "1" : "0");
#endif
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
        if (!strcmp(tok, "mode")) {
            struct revela_frame f;
            memset(&f, 0, sizeof f);
            snprintf(f.mode, sizeof f.mode, "%s", eq + 1);
            const struct revela_sensor_mode *m = find_mode(&f);
            if (!m) {
                snprintf(err, cap, "err mode %s: the sensor module has no "
                         "such mode (modes lists them)", eq + 1);
                return -1;
            }
            snprintf(c->frame.mode, sizeof c->frame.mode, "%s", m->name);
            c->frame.width = m->width;
            c->frame.height = m->height;
            c->frame.bits = m->bits;
            c->frame.bayer_phase = m->bayer_phase;
            continue;
        }
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
        if (cur.frame.mode[0])
            n += snprintf(reply + n, cap - n, " mode=%s", cur.frame.mode);
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
    if (!strcmp(line, "modes")) {
        /* the sensor module's modes: name=WxHxBITS, then its Bayer phase,
         * pixel rate, and default line and frame length */
        const struct revela_sensor *s = sensor();
        int n = snprintf(reply, cap, "ok");
        for (uint32_t i = 0; s && i < s->n_modes && n > 0 &&
             (uint32_t)n < cap; i++) {
            const struct revela_sensor_mode *m = &s->modes[i];
            n += snprintf(reply + n, cap - n, " %s=%ux%ux%u,%u,%u,%u,%u",
                          m->name, (unsigned)m->width, (unsigned)m->height,
                          (unsigned)m->bits, (unsigned)m->bayer_phase,
                          (unsigned)m->pixel_rate_hz,
                          (unsigned)m->line_length_pck,
                          (unsigned)m->frame_length_lines);
        }
        return 0;
    }
    snprintf(reply, cap, "err unknown command; the firmware speaks: init "
             "<field>=<value>..., configure <field>=<value>..., set <param> "
             "<value>, get <param>, status, config, modes");
    return -1;
}
