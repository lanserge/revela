/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * revela_fw.h -- the actions the firmware offers whoever drives it.
 *
 * The firmware knows how to bring up and run the ISP, its sensor and its
 * display at a configuration it is GIVEN. It keeps no table of modes and
 * no sequence of its own: which sensor mode, which display raster and
 * which 3A settings to use is the caller's knowledge -- a Pi's web
 * panel, an MCU's host -- passed in as numbers. Each action applies what
 * it is given or refuses it, saying why, and changes nothing on refusal.
 */
#ifndef REVELA_FW_H
#define REVELA_FW_H

#include <stdint.h>
#include "revela_hal.h"

/* The sensor's frame as it is read out. */
struct revela_frame {
    char mode[32];              /* the sensor module's mode it comes from,
                                   by name; empty: the mode of this size */
    uint32_t width, height;     /* samples, lines */
    uint32_t bits;              /* sample depth */
    uint32_t bayer_phase;       /* the CFA phase at the frame's origin:
                                   bit 0 a column swap, bit 1 a row swap */
};                              /* its timing is the sensor's own mode's:
                                   nothing downstream is told a rate */

/* Where the picture sits in the raster; center derives the offsets. */
struct revela_window {
    uint32_t x0, y0, width, height;
    uint8_t  center;
};

/* 3A: each loop on or off, and what it aims for. */
struct revela_3a {
    uint8_t  ae, awb, af;       /* 1: the loop runs */
    uint32_t ae_target;         /* mean level aimed for, at the ISP's depth */
    uint32_t exposure_max;      /* lines; 0: as long as the frame allows */
    uint32_t gain_max;          /* analogue gain code; 0: the sensor's max */
};

struct revela_config {
    struct revela_frame  frame;
    struct revela_raster display;
    struct revela_window window;
    struct revela_3a     aaa;
};

/* What a frame tells the firmware, once a frame. */
struct revela_tick {
    uint32_t seq;               /* frames since the stream started */
};

/* The platform's HAL, once, before anything else. */
void revela_fw_attach(const struct revela_hal *hal);

/* Bring everything up at `cfg`: check the ISP is the build its map came
 * from, load its settings and the sensor's profile, set the frame, the
 * picture and the display, and bring up whichever 3A modules are
 * present. 0, or negative with the reason logged. */
int revela_fw_init(const struct revela_config *cfg);

/* Change a running system to `cfg`, whole: the new configuration lands
 * together or not at all. */
int revela_fw_configure(const struct revela_config *cfg);

/* Once a frame: the 3A loops run here. */
void revela_fw_tick(const struct revela_tick *t);

/* A named parameter (an ISP register by its path, a profile value):
 * what a panel's controls read and write. */
int revela_fw_param_set(const char *key, int64_t value);
int revela_fw_param_get(const char *key, int64_t *value);

/* One line of status: what is applied, what was refused and why, and
 * where the 3A loops stand. */
int revela_fw_status(char *line, uint32_t cap);

/* The same actions as one line of text, for whatever console reaches
 * the firmware -- a Pi's control socket, an MCU's UART:
 *   init <key>=<value> ...        configure <key>=<value> ...
 *   set <param> <value>   get <param>   status
 *   config         the current configuration, as init takes it back
 * A configuration's keys are the config's fields by their C names
 * (frame.width=1920 display.h_total=2200 aaa.ae=1 ...); a field not
 * given keeps its current value. The reply is "ok ..." or "err ...". */
int revela_fw_command(const char *line, char *reply, uint32_t cap);

#endif
