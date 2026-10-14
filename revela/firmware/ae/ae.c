/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * ae.c -- auto exposure. A STUB, standing in until the statistics reach
 * the firmware: it measures nothing and runs no loop. When the AE
 * settings say on, it starts the sensor from a defined point -- its own
 * declared unity gain and its longest integration that fits a frame --
 * so a run never inherits whatever the last caller left (on 2026-08-23
 * that was 12.5x gain, and the raw arrived 55% saturated). The values
 * are the sensor module's facts, never this file's.
 */
#include <stdint.h>
#include <stdio.h>

#include "revela_algo.h"

static const struct revela_algo_env *env;

static int ae_init(const struct revela_algo_env *e) {
    env = e;
    return 0;
}

static void ae_configure(const struct revela_3a *aaa,
                         const struct revela_frame *frame) {
    (void)frame;
    if (!aaa->ae || !env->sensor) return;
    uint32_t gain = env->sensor->gain_unity_code;
    uint32_t lines = env->sensor->integration_default_lines;
    if (aaa->gain_max && gain > aaa->gain_max) gain = aaa->gain_max;
    /* the longest integration that fits the frame, as whoever knows the
     * frame's length says: past it the sensor stretches the frame */
    if (aaa->exposure_max && lines > aaa->exposure_max)
        lines = aaa->exposure_max;
    int ok = 0;
    if (env->hal->sensor_analog_gain && env->hal->sensor_analog_gain(gain) == 0)
        ok |= 1;
    if (lines && env->hal->sensor_exposure &&
        env->hal->sensor_exposure(lines) == 0)
        ok |= 2;
    if (env->hal->log) {
        char line[120];
        snprintf(line, sizeof line, "ae: started at gain code %u and %u "
                 "lines%s -- a stub until the statistics arrive",
                 (unsigned)gain, (unsigned)lines, ok == 3 ? "" : " (not all "
                 "taken)");
        env->hal->log(line);
    }
}

static void ae_tick(const struct revela_tick *t) { (void)t; }

static const struct revela_algo ae = {
    .api = REVELA_ALGO_API, .name = "ae stub",
    .init = ae_init, .configure = ae_configure, .tick = ae_tick,
};

const struct revela_algo *revela_ae(void) { return &ae; }
