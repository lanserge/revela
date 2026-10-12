/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * revela_sensor.h -- what a sensor module carries: its facts and its
 * fitted ISP profile, as data. It drives nothing: the platform's sensor
 * driver does, through the HAL, by what it is asked; this is what the
 * firmware must know to ask well.
 */
#ifndef REVELA_SENSOR_H
#define REVELA_SENSOR_H

#include <stdint.h>

#define REVELA_SENSOR_API 4

/* fitted ISP parameters, keyed <composite>/<path relative to it>
 * ("revela.pipelines.bayer_isp:bayer_isp/ccm.m_0_0") -- semantic keys,
 * never addresses, anchored by the core wherever that composite sits in
 * its build; a NULL key ends the list */
struct revela_profile_entry {
    const char *key;
    int32_t value;
};

/* The sensor's LAWS: what no driver call can answer. gain = code /
 * gain_unity_code; exposure_max_margin lines must remain between the
 * integration and the frame length; the apply delays are in frames. */
struct revela_sensor_facts {
    uint32_t gain_unity_code, gain_min_code, gain_max_code;
    uint32_t exposure_min_lines, exposure_max_margin;
    uint32_t integration_default_lines;
    uint32_t delay_exposure, delay_analog_gain, delay_digital_gain;
    int32_t black_level;        /* the pedestal, at the sensor's depth */
    uint32_t focus_min, focus_max, focus_step, focus_settle_ms;
                                /* all 0: fixed focus */
};

struct revela_sensor {
    int api;                    /* REVELA_SENSOR_API */
    const char *name;
    const struct revela_profile_entry *profile;
    const struct revela_sensor_facts *facts;    /* NULL: unstated */
};

/* implemented by the sensor module, weak in the core: absent, NULL */
const struct revela_sensor *revela_sensor(void);

#endif
