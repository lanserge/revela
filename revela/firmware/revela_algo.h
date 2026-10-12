/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * revela_algo.h -- the contract between the firmware's core and a 3A
 * module: AE, AWB or AF, each its own module, each optional.
 *
 * A module is computation. It touches no hardware of its own: the core
 * lends it the sensor's controls (the platform's, through the HAL), the
 * ISP's parameters by name (through the core, the one writer), and the
 * sensor's facts; and calls it once a frame and whenever the 3A
 * settings change. An absent module is a loop that does not run.
 */
#ifndef REVELA_ALGO_H
#define REVELA_ALGO_H

#include <stdint.h>
#include "revela_hal.h"
#include "revela_fw.h"
#include "revela_sensor.h"

#define REVELA_ALGO_API 2

struct revela_algo_env {
    const struct revela_hal *hal;               /* sensor controls, time, log */
    const struct revela_sensor_facts *sensor;   /* NULL: unstated */
    int (*isp_set)(const char *key, int32_t value);
    int (*isp_get)(const char *key, int32_t *value);
    int (*isp_commit)(void);
};

struct revela_algo {
    int api;                                    /* REVELA_ALGO_API */
    const char *name;
    int  (*init)(const struct revela_algo_env *env);
    /* the 3A settings, at init and at every change */
    void (*configure)(const struct revela_3a *aaa,
                      const struct revela_frame *frame);
    void (*tick)(const struct revela_tick *t);
};

/* each implemented by its module, and weak in the core: absent, NULL */
const struct revela_algo *revela_ae(void);
const struct revela_algo *revela_awb(void);
const struct revela_algo *revela_af(void);

#endif
