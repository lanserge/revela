/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * revela_hal.h -- what a platform lends the firmware.
 *
 * The firmware is portable C: no operating system, only the C library's
 * strings and formatting. It reaches hardware through the calls below
 * and nothing else. A Pi beside the board under test implements them
 * one way (the ISP over the display cable's I2C, the sensor through its
 * Linux driver); an MCU beside the ISP another (the ISP on its own bus,
 * the sensor through a driver of its own, below this line). Every call
 * returns 0 on success and a negative value on failure, which the
 * firmware expects: cables are cables, buses are buses.
 */
#ifndef REVELA_HAL_H
#define REVELA_HAL_H

#include <stdint.h>

struct revela_hal {
    /* The ISP's registers -- and the display raster's beside them -- at
     * the addresses the firmware's map states. */
    int (*isp_read)(uint32_t addr, uint32_t *value);
    int (*isp_write)(uint32_t addr, uint32_t value);

    /* The sensor, by what it is asked to do, never by its registers: a
     * platform's sensor driver owns those. sensor_mode sets the frame
     * (size, depth, line time, frame length); NULL ops are ones the
     * platform cannot offer. */
    int (*sensor_mode)(uint32_t width, uint32_t height, uint32_t bits,
                       uint32_t line_ns, uint32_t frame_lines);
    int (*sensor_exposure)(uint32_t lines);
    int (*sensor_analog_gain)(uint32_t code);
    int (*sensor_digital_gain)(uint32_t code);
    int (*sensor_focus)(uint32_t position);

    /* Time and words. */
    void (*sleep_us)(uint32_t us);
    uint64_t (*now_us)(void);
    void (*log)(const char *line);
};

#endif
