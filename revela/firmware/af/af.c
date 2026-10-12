/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * af.c -- auto focus. A STUB: it comes up, takes its settings and runs once a
 * frame, doing nothing, until the statistics reach the firmware. It
 * holds the module's place, so the AF module loads, configures and
 * ticks end to end before it has anything to compute.
 */
#include <stdint.h>

#include "revela_algo.h"

static const struct revela_algo_env *env;

static int af_init(const struct revela_algo_env *e) {
    env = e;
    return 0;
}

static void af_configure(const struct revela_3a *aaa,
                          const struct revela_frame *frame) {
    (void)aaa;
    (void)frame;
}

static void af_tick(const struct revela_tick *t) { (void)t; }

static const struct revela_algo af = {
    .api = REVELA_ALGO_API, .name = "af stub",
    .init = af_init, .configure = af_configure, .tick = af_tick,
};

const struct revela_algo *revela_af(void) { return &af; }
