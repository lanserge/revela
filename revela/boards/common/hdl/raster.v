// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// raster -- a pixel stream becomes a timed raster, genlocked to it.
//
// The mirror of a link receiver, which follows whatever the sender
// states; this one DEFINES a link -- it drives a display, so its timing
// is never negotiated with the sink. The mode (totals, active area, sync
// windows and polarities) arrives on inputs, set by whoever set the
// camera's mode, and the caller holds them steady; the parameters are
// capacity only: how wide a pixel, how long a line and a frame can be.
//
// A FRAME MAY BE DROPPED, NEVER DISPLACED. If the stream has not
// delivered the frame's first beat when the picture's first pixel is
// due, starting anyway paints the WHOLE frame displaced by the gap --
// and nothing downstream can detect it. So the raster ARMS every frame:
// it starts a frame only once that frame's first beat is at the head,
// and a late stream costs one frame of fill.
//
// A WINDOW THAT DOES NOT FIT IS REFUSED, VISIBLY, never clipped: a
// sticky status bit says so.
//
// DATA AND DATA-ENABLE LEAVE TOGETHER: one register stage for the pixel,
// the enable and both syncs. A skew of one clock shows a picture
// shifted by one, and no status bit can see it from inside.
//
// GENLOCK BY CLOCK STEERING. The raster's geometry never moves -- a
// television drops sync on any vertical-total motion, however slow
// (bench-paid: one line per frame held fabric lock perfectly and lost
// the TV entirely; a fixed raster on the same clock was solid). The
// stream is followed through the MMCM's fine phase shifter instead,
// ~24 ps a step, cumulative: a fractional frequency trim with hundreds
// of ppm of authority. Phase is measured at each frame start's arrival
// at the queue head, in pixels, against a target G_LEAD lines before
// the window; small errors drain through the phase shifter, invisible
// to any sink. An error past two lines is not steerable at 24 ps: the
// counters JAM to the target once, under a cooldown -- one visibly short
// frame, the price of a mode set. Crystal ppm is the genlock's job; a
// frame-rate change is a mode change, and the caller's.
module raster #(
    parameter DW = 24,                  // one pixel, on the stream and the sink (<= 32)
    parameter XB = 12,                  // bits of a line's total
    parameter YB = 11,                  // bits of a frame's total
    parameter [DW-1:0] FILL = 24'h101010,
    // How early the frame start should arrive, in lines. THIS IS
    // STORAGE: with no frame store, every line of lead is a line the
    // elastic upstream must hold.
    parameter G_LEAD = 2,
    // SELFTEST paints from the raster's OWN counters and ignores the
    // stream: is the data this block emits where its own enable says
    // it is? A displacement that survives this is downstream of here.
    parameter SELFTEST = 0
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          locked,        // the pixel clock's own testimony
    // the picture, as an elastic stream
    input  wire [DW-1:0] s_axis_tdata,
    input  wire          s_axis_tvalid,
    output wire          s_axis_tready,
    input  wire          s_axis_tuser,  // start of frame
    input  wire          s_axis_tlast,  // end of line (the raster rules)
    // the mode, positions counted from the first active pixel and line
    input  wire [XB-1:0] h_total,
    input  wire [XB-1:0] h_active,
    input  wire [XB-1:0] hs_beg,
    input  wire [XB-1:0] hs_end,
    input  wire [YB-1:0] v_total,
    input  wire [YB-1:0] v_active,
    input  wire [YB-1:0] vs_beg,
    input  wire [YB-1:0] vs_end,
    input  wire          hs_pos,
    input  wire          vs_pos,
    // fine phase-shift steps in one pixel of this clock: a fact of the
    // MMCM that makes it (56 per VCO period, times its output divider)
    input  wire [15:0]   ps_per_px,
    // WHERE the picture goes, and how big it is
    input  wire [15:0]   win_x0,
    input  wire [15:0]   win_y0,
    input  wire [15:0]   win_w,
    input  wire [15:0]   win_h,
    input  wire          center,        // derive the offsets instead
    input  wire          follow,        // 1: steer to the stream; 0: hold
    output reg           ps_en,         // one PSEN pulse per step
    output reg           ps_incdec,     // 1: delay (retard the raster)
    input  wire          ps_done,
    // where the picture ACTUALLY landed -- read-only, because a derived
    // value written back into its register becomes an explicit ask
    output wire [15:0]   act_x0,
    output wire [15:0]   act_y0,
    output reg           vid_active_video,
    output reg  [DW-1:0] vid_data,
    output reg           vid_hsync,
    output reg           vid_vsync,
    output wire [14:0]   status
);
    localparam PB = XB + YB;            // bits of a frame's pixel count
    localparam OB = XB + 1 + 16;        // bits of the steering debt

    reg [XB-1:0] x;
    reg [YB-1:0] y;

    // The window, as the hardware sees it. Centring is a shift, not a
    // divider, and it is DERIVED -- never stored back into the register
    // the host wrote.
    wire [16:0] h_act = {{(17-XB){1'b0}}, h_active};
    wire [16:0] v_act = {{(17-YB){1'b0}}, v_active};
    wire [16:0] c_x0 = (h_act > win_w) ? ((h_act - win_w) >> 1) : 17'd0;
    wire [16:0] c_y0 = (v_act > win_h) ? ((v_act - win_h) >> 1) : 17'd0;
    wire [16:0] w_x0_n = center ? c_x0 : {1'b0, win_x0};
    wire [16:0] w_y0_n = center ? c_y0 : {1'b0, win_y0};
    wire [17:0] x_end_n = w_x0_n + win_w;
    wire [17:0] y_end_n = w_y0_n + win_h;
    wire fits_n = (win_w != 0) && (win_h != 0)
               && (x_end_n <= h_act) && (y_end_n <= v_act);

    // Every one of those is FRAME-CONSTANT, so it is worked out once and
    // compared against, not worked out again beneath every pixel: the
    // first build that put placement on registers missed setup by 124 ps
    // with that arithmetic under the pixel path. The mode's own derived
    // numbers are frame-constant too, and get the same treatment.
    reg [16:0] w_x0 = 17'd0, w_y0 = 17'd0;
    reg [17:0] x_end = 18'd0, y_end = 18'd0;
    reg fits = 1'b0;
    reg [PB-1:0] frame_px = 0;          // pixels in a frame
    reg [XB:0]   two_lines = 0;         // pixels in two lines
    reg [OB-1:0] owed_cap = 0;          // the most debt one arrival sets
    always @(posedge clk) begin
        w_x0  <= w_x0_n;  w_y0  <= w_y0_n;
        x_end <= x_end_n; y_end <= y_end_n;
        fits  <= fits_n;
        frame_px  <= h_total * v_total;
        two_lines <= {h_total, 1'b0};
        owed_cap  <= {h_total, 1'b0} * ps_per_px;
    end
    // the readback follows the copy the raster is USING
    assign act_x0 = w_x0[15:0];
    assign act_y0 = w_y0[15:0];

    wire active = (x < h_active) && (y < v_active);
    wire blank  = (y >= v_active);
    wire in_win = fits && active
               && (x >= w_x0) && (x < x_end)
               && (y >= w_y0) && (y < y_end);
    wire first_px = in_win && (x == w_x0[XB-1:0]) && (y == w_y0[YB-1:0]);
    wire last_px  = in_win && (x == (x_end - 1)) && (y == (y_end - 1));
    wire line_end  = (x >= h_total - 1'b1);
    wire frame_end = line_end && (y >= v_total - 1'b1);

    reg [15:0] in_period;               // line ticks since the last arrival
    reg head_sof_d;                     // one arrival edge per input frame
    // the steering debt: steps still to issue, OVERWRITTEN at each
    // arrival (a proportional re-target; the slew-limited drain is the
    // smoothing)
    reg [OB-1:0] owed;
    reg owed_ret;                       // 1: retard the raster (delay)
    reg ps_busy;                        // one step in flight
    reg [4:0] ps_tmo;                   // PSDONE comes ~12 PSCLKs later; a
                                        // miswired done pin degrades to slow
    reg [5:0] jam_cool;                 // frames since the last jam: a wild
                                        // source must not jam every frame
    reg glocked;                        // STATUS, not a control state
    reg [2:0] lock_run;

    reg armed;                          // this frame's first beat is HERE
    reg ever_armed;
    wire head_sof = s_axis_tvalid && s_axis_tuser;
    // ONE consumption rule: window positions consume, and nothing else
    // does. An EARLY frame start waits, because blanking has no window
    // positions to consume it; a LATE one passes mid-window, where its
    // position IS the error. Blanking also DRAINS beats that are not a
    // frame start: without that, any transient where the raster runs
    // long grows the backlog forever. Converged, the drain is a no-op.
    assign s_axis_tready = in_win || (blank && !head_sof);

    // the measurement: the frame start reaching the queue head is the
    // arrival, once per input frame. Target: G_LEAD lines before the
    // window origin, waited out in blanking.
    wire [YB-1:0] pass_dy = y - w_y0[YB-1:0];
    wire sof_arr = head_sof && !head_sof_d;
    wire [YB:0] wy0 = w_y0[YB:0];
    wire [YB:0] vtot = {1'b0, v_total};
    wire [YB:0] arr_tgt = (wy0 >= G_LEAD) ? (wy0 - G_LEAD)
                                          : (vtot + wy0 - G_LEAD);
    // signed-shortest-way phase error, in whole lines
    wire [YB:0] arr_dy = ({1'b0, y} >= arr_tgt) ? ({1'b0, y} - arr_tgt)
                                                : ({1'b0, y} + vtot - arr_tgt);
    wire arr_late = (arr_dy != 0) && (arr_dy < (vtot >> 1));
    wire [YB:0] arr_err = arr_late ? arr_dy
                        : (arr_dy == 0) ? {(YB+1){1'b0}}
                        : (vtot - arr_dy);
    // the same error in PIXELS, what the phase shifter can steer, in a
    // REGISTERED pipeline, one multiply a cycle: the single-cycle cone
    // missed timing by 2.3 ns. Once a frame, so the latency is free.
    reg [XB-1:0] arr_x;
    reg [YB-1:0] arr_y;
    reg [2:0] calc;                     // one-hot measurement pipeline
    reg [PB:0] pos_r, tgt_r, err_r;
    reg late_r;
    wire [PB:0] fpx = {1'b0, frame_px};
    wire [PB:0] half = fpx >> 1;

    reg [24:0] alive;
    reg [3:0]  vsyncs;
    reg vs_d, underflow, misalign, refused;
    reg [2:0] sof_run, sof_cnt;
    wire [31:0] selftest_px = {4{x[7:0]}};
    always @(posedge clk) begin
        alive <= alive + 1;
        if (rst || !locked) begin
            x <= 0; y <= 0; armed <= 0; ever_armed <= 0;
            in_period <= 0; head_sof_d <= 0;
            owed <= 0; owed_ret <= 0; calc <= 0;
            ps_en <= 0; ps_incdec <= 0;
            ps_busy <= 0; ps_tmo <= 0; jam_cool <= 0;
            glocked <= 0; lock_run <= 0;
            vid_active_video <= 0; vid_hsync <= 0; vid_vsync <= 0;
            vid_data <= FILL; vsyncs <= 0; vs_d <= 0;
            underflow <= 0; misalign <= 0; refused <= 0;
            sof_run <= 0; sof_cnt <= 0;
        end else begin
            x <= line_end ? {XB{1'b0}} : x + 1'b1;
            if (line_end)
                y <= frame_end ? {YB{1'b0}} : y + 1'b1;

            // ---- the genlock: the clock is steered, the counters are
            // still. Each arrival re-targets the debt; the stepper drains
            // it one fine-phase step at a time.
            if (line_end) begin
                in_period <= (in_period == 16'hFFFF) ? in_period
                                                     : in_period + 1'b1;
                if (frame_end && jam_cool != 0)
                    jam_cool <= jam_cool - 1'b1;
            end
            if (s_axis_tvalid && s_axis_tready && s_axis_tuser) begin
                // lock is a REPORT: frame starts passing at the window
                // origin, four in a row
                if (pass_dy == {YB{1'b0}}) begin
                    if (lock_run != 3'd7) lock_run <= lock_run + 3'd1;
                    glocked <= (lock_run >= 3'd3);
                end else begin
                    lock_run <= 0; glocked <= 0;
                end
            end
            // the stepper: one PSEN pulse, then wait for PSDONE
            ps_en <= 1'b0;
            if (ps_busy) begin
                if (ps_done || ps_tmo == 0) ps_busy <= 0;
                else ps_tmo <= ps_tmo - 5'd1;
            end else if (follow && owed != 0) begin
                ps_en <= 1'b1;
                ps_incdec <= owed_ret;
                ps_busy <= 1'b1; ps_tmo <= 5'd31;
                owed <= owed - 1'b1;
            end
            head_sof_d <= head_sof;
            if (sof_arr) begin
                in_period <= 0;
                if (follow) begin
                    if (arr_err > 2) begin
                        // beyond steering: jam ONCE, cooled
                        if (jam_cool == 0) begin
                            x <= {XB{1'b0}};
                            y <= arr_tgt[YB-1:0];
                            jam_cool <= 6'd63;
                        end
                        owed <= 0; calc <= 3'b000;
                    end else begin
                        // within steering: capture the arrival, measure
                        // over the next three cycles
                        arr_x <= x; arr_y <= y;
                        calc <= 3'b001;
                    end
                end
            end
            if (calc[0]) begin
                pos_r <= arr_y * h_total + arr_x;
                tgt_r <= arr_tgt * h_total;
                calc <= 3'b010;
            end
            if (calc[1]) begin
                if (pos_r >= tgt_r) begin
                    late_r <= (pos_r - tgt_r) < half;
                    err_r <= ((pos_r - tgt_r) < half) ? (pos_r - tgt_r)
                                                      : (fpx - (pos_r - tgt_r));
                end else begin
                    late_r <= (pos_r + fpx - tgt_r) < half;
                    err_r <= ((pos_r + fpx - tgt_r) < half) ? (pos_r + fpx - tgt_r)
                                                           : (tgt_r - pos_r);
                end
                calc <= 3'b100;
            end
            if (calc[2]) begin
                if (err_r > 1) begin
                    // past two lines the debt is capped, so the multiply
                    // only ever sees an error that fits two lines
                    owed <= (err_r > two_lines) ? owed_cap
                                                : err_r[XB:0] * ps_per_px;
                    owed_ret <= late_r;
                end else begin
                    owed <= 0;
                end
                calc <= 3'b000;
            end
            // source gone: stop steering and say so. The raster needs no
            // restoring -- it never moved.
            if (in_period > {v_total, 2'b00}) begin
                glocked <= 0; lock_run <= 0; owed <= 0;
                calc <= 0;
            end

            // ONE register stage for pixel, enable and both syncs
            vid_hsync <= hs_pos ? ((x >= hs_beg) && (x < hs_end))
                                : !((x >= hs_beg) && (x < hs_end));
            vid_vsync <= vs_pos ? ((y >= vs_beg) && (y < vs_end))
                                : !((y >= vs_beg) && (y < vs_end));
            vid_active_video <= active;
            if (SELFTEST)
                vid_data <= (x < 4 || x >= h_active - 4) ? {DW{1'b1}}
                                                         : selftest_px[DW-1:0];
            else
                vid_data <= (armed && in_win && s_axis_tvalid)
                            ? s_axis_tdata : FILL;

            vs_d <= vid_vsync;
            if (vid_vsync && !vs_d) vsyncs <= vsyncs + 4'd1;
            if (!fits) refused <= 1;

            // Arm on the frame start, in the blanking before it is due;
            // disarm when the window's last pixel is spent, so the next
            // frame must arm again. Alignment is per FRAME. A late frame
            // start consumed mid-window arms too: the joined frame shows
            // from where it joins.
            if (blank && head_sof) begin armed <= 1; ever_armed <= 1; end
            if (s_axis_tvalid && s_axis_tready && s_axis_tuser)
                begin armed <= 1; ever_armed <= 1; end
            if (armed && last_px) armed <= 1'b0;
            if (armed && in_win && !s_axis_tvalid) underflow <= 1;
            if (armed && first_px && s_axis_tvalid && !s_axis_tuser)
                misalign <= 1;
            if (head_sof && s_axis_tready && sof_run != 3'd7)
                sof_run <= sof_run + 3'd1;
            if (first_px) begin
                sof_cnt <= sof_run;
                sof_run <= (head_sof && s_axis_tready) ? 3'd1 : 3'd0;
            end
        end
    end
    // sof-per-frame [2:0], locked, armed, underflow, misalign, refused,
    // alive [10:8], vsyncs [13:11], the genlock's own lock at 14
    assign status = {glocked, vsyncs[2:0], alive[24:22], refused, misalign,
                     underflow, ever_armed, locked, sof_cnt};
endmodule
