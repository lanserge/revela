/* Copyright 2026 Serge Rabyking
 * SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
 *
 * pi_glue.c -- the firmware on a Pi: the HAL over picam2hdmi's bridge,
 * and the bridge's hooks turned into the firmware's actions. Not
 * firmware: the platform's half, which an MCU replaces with its own.
 *
 *   ISP registers   over the display cable's I2C (bridge_ddc_*)
 *   sensor          its Linux driver, through V4L2 controls on the
 *                   subdevice the bridge holds; its MODE is the bridge's,
 *                   set at start (--mode), so a different frame is
 *                   refused here and asked of the bridge instead
 *   console         the bridge's control socket: the firmware's commands
 *   every frame     revela_fw_tick
 *   link up         the last configuration, replayed -- the board forgets
 *                   its registers when the cable or its power goes, and
 *                   putting them back is this harness's policy
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <linux/videodev2.h>

#include "../../bridge.h"
#include "revela_fw.h"

static int sensor_fd = -1;
static struct bridge_mode mode;         /* the bridge's, at the last edge */
static int have_mode;
static char replay[2048];               /* "init <config>", once applied */

static int isp_read(uint32_t addr, uint32_t *value) {
    return addr > 0xFFFF ? -1 : bridge_ddc_read((uint16_t)addr, value);
}
static int isp_write(uint32_t addr, uint32_t value) {
    return addr > 0xFFFF ? -1 : bridge_ddc_write((uint16_t)addr, value);
}

static int sensor_mode(uint32_t width, uint32_t height, uint32_t bits) {
    (void)bits;
    if (have_mode && width == mode.width && height == mode.height) return 0;
    printf("revela: the Pi's sensor is %ux%u; a %ux%u frame is asked of "
           "the bridge (--mode), which restarts on it\n",
           (unsigned)mode.width, (unsigned)mode.height, (unsigned)width,
           (unsigned)height);
    return -1;
}

static int ctrl(uint32_t id, int32_t value) {
    if (sensor_fd < 0) return -1;
    struct v4l2_control c = {.id = id, .value = value};
    return ioctl(sensor_fd, VIDIOC_S_CTRL, &c) < 0 ? -1 : 0;
}
static int exposure(uint32_t lines) { return ctrl(V4L2_CID_EXPOSURE, lines); }
static int analog_gain(uint32_t code) {
    return ctrl(V4L2_CID_ANALOGUE_GAIN, code);
}
static int digital_gain(uint32_t code) {
    return ctrl(V4L2_CID_DIGITAL_GAIN, code);
}

static void sleep_us(uint32_t us) { usleep(us); }
static uint64_t now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}
static void log_line(const char *line) { printf("%s\n", line); }

static const struct revela_hal pi = {
    .isp_read = isp_read, .isp_write = isp_write,
    .sensor_mode = sensor_mode, .sensor_exposure = exposure,
    .sensor_analog_gain = analog_gain, .sensor_digital_gain = digital_gain,
    .sensor_focus = NULL,
    .sleep_us = sleep_us, .now_us = now_us, .log = log_line,
};

static void attach(void) {
    static int done;
    if (!done) { revela_fw_attach(&pi); done = 1; }
}

/* after an init or configure the firmware took: what to replay */
static void remember(void) {
    char cfg[2040];
    if (revela_fw_command("config", cfg, sizeof cfg) == 0 &&
        !strncmp(cfg, "ok", 2))
        snprintf(replay, sizeof replay, "init%s", cfg + 2);
}

void bridge_frame_hook(const struct bridge_frame *f) {
    attach();
    sensor_fd = f->sensor_fd;
    struct revela_tick t = {.seq = f->seq};
    revela_fw_tick(&t);
}

void bridge_link_hook(const struct bridge_link *l) {
    attach();
    if (l->mode) { mode = *l->mode; have_mode = 1; }
    if (!l->up || !l->edid || !replay[0]) return;
    int ours = 0;
    for (int i = 0; i + 9 < 256; i++)
        if (!memcmp(l->edid + i, "BAYERLINK", 9)) { ours = 1; break; }
    if (!ours) {
        printf("revela: the sink is not a bayerlink receiver; leaving it "
               "alone\n");
        return;
    }
    char reply[256];
    revela_fw_command(replay, reply, sizeof reply);
    printf("revela: link up, the configuration replayed: %s\n", reply);
}

void bridge_command_hook(const char *cmd, char *reply, size_t cap) {
    attach();
    int rc = revela_fw_command(cmd, reply, (uint32_t)cap);
    if (rc == 0 && (!strncmp(cmd, "init", 4) || !strncmp(cmd, "configure", 9)))
        remember();
}
