/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * revela_sensor.h -- what a sensor module carries: its facts, its
 * modes and its fitted ISP profile, as data, from the sensor's own
 * description (revela/sensors/<name>/sensor.json). It drives nothing
 * itself. A mode is everything selecting it takes -- the register writes
 * included -- so a system with no sensor driver (an MCU, an ASIC, an ISP
 * on its own MIPI input) selects one from this alone, and a platform that
 * does have a driver is told which mode to hold.
 */
#ifndef REVELA_SENSOR_H
#define REVELA_SENSOR_H

#include <stdint.h>

#define REVELA_SENSOR_API 5

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

/* one register write, as the sensor's bus carries it */
struct revela_sensor_write {
    uint16_t reg;
    uint8_t value;
};

/* A readout mode: the frame it delivers, its timing at its defaults (line
 * and frame length are the run-time controls' from there), and the writes
 * that select it. bayer_phase is the order this mode reads out: 0 RGGB,
 * 1 GRBG, 2 GBRG, 3 BGGR. */
struct revela_sensor_mode {
    const char *name;
    uint32_t width, height, bits;
    uint32_t bayer_phase;
    uint32_t pixel_rate_hz, line_length_pck, frame_length_lines;
    const struct revela_sensor_write *writes;
    uint32_t n_writes;
};

struct revela_sensor {
    int api;                    /* REVELA_SENSOR_API */
    const char *name;
    const struct revela_profile_entry *profile;
    const struct revela_sensor_facts *facts;    /* NULL: unstated */
    const struct revela_sensor_mode *modes;     /* n_modes of them */
    uint32_t n_modes;
};

/* implemented by the sensor module, weak in the core: absent, NULL */
const struct revela_sensor *revela_sensor(void);

#endif
