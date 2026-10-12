// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// scanout -- the raster, taking its mode and placement from a register
// file on another clock.
//
// The register file runs on the BUS clock, never the raster's. AXI has no
// timeout: a slave whose clock stops never answers and the processor
// waits for it forever (this board hung that way on 2026-08-17, when the
// raster still ran on the link's recovered clock).
//
// So the values cross clocks, and a wide value crossing is the classic
// way to sample a number that never existed. The file's ARM does that job
// without a second bank: software writes the mode and the placement, then
// the arm; while armed the file refuses further writes, so this side
// takes its copy at leisure and acknowledges. Only the arm and its
// acknowledgement cross.
//
// The copy is taken in VERTICAL BLANKING: mode and placement decide where
// every pixel of a frame lands, so taking them mid-frame would tear the
// frame in two. Before the first copy there is no mode to blank in, so
// the first is taken at once and the raster is held until it has one --
// the file resets armed, so a bitstream nobody writes to still takes the
// mode it was built with. The copy is kept through a raster reset: the
// mode is the register file's, not the raster clock's.
module scanout #(
    parameter DW = 24,
    parameter XB = 12,
    parameter YB = 11,
    parameter [DW-1:0] FILL = 24'h101010,
    parameter G_LEAD = 2
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis:vid, ASSOCIATED_RESET rst" *)
    input  wire          clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst RST", X_INTERFACE_PARAMETER = "POLARITY ACTIVE_HIGH" *)
    input  wire          rst,
    input  wire          locked,
    // the register file's values, on the bus clock, held while armed
    input  wire [15:0]   reg_h_total,
    input  wire [15:0]   reg_h_active,
    input  wire [15:0]   reg_hs_beg,
    input  wire [15:0]   reg_hs_end,
    input  wire [15:0]   reg_v_total,
    input  wire [15:0]   reg_v_active,
    input  wire [15:0]   reg_vs_beg,
    input  wire [15:0]   reg_vs_end,
    input  wire          reg_hs_pos,
    input  wire          reg_vs_pos,
    input  wire [15:0]   reg_ps_per_px,
    input  wire [15:0]   reg_win_x0,
    input  wire [15:0]   reg_win_y0,
    input  wire [15:0]   reg_win_w,
    input  wire [15:0]   reg_win_h,
    input  wire          reg_center,
    input  wire          arm_req,
    output reg           arm_ack,
    // 1: the raster follows the stream (the direct picture is on); 0: it
    // holds its mode unsteered, painting the fill
    input  wire          param_follow,
    output wire          ps_en,
    output wire          ps_incdec,
    input  wire          ps_done,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TDATA" *)
    input  wire [DW-1:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TVALID" *)
    input  wire          s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TREADY" *)
    output wire          s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TUSER" *)
    input  wire          s_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 s_axis TLAST" *)
    input  wire          s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid ACTIVE_VIDEO" *)
    output wire          vid_active_video,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid DATA" *)
    output wire [DW-1:0] vid_data,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid HSYNC" *)
    output wire          vid_hsync,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid VSYNC" *)
    output wire          vid_vsync,
    output wire [14:0]   status
);
    (* ASYNC_REG = "TRUE" *) reg req_s0 = 1'b0, req_s1 = 1'b0;
    reg have = 1'b0;                    // a copy has been taken
    reg [XB-1:0] h_total, h_active, hs_beg, hs_end;
    reg [YB-1:0] v_total, v_active, vs_beg, vs_end;
    reg          hs_pos, vs_pos, center;
    reg [15:0]   ps_per_px, win_x0, win_y0, win_w, win_h;
    initial arm_ack = 1'b0;

    wire in_vblank = (vid_vsync == vs_pos) && !vid_active_video;
    always @(posedge clk) begin
        if (rst) begin
            req_s0 <= 1'b0; req_s1 <= 1'b0; arm_ack <= 1'b0;
        end else begin
            req_s0 <= arm_req; req_s1 <= req_s0;
            if (req_s1 && !arm_ack && (in_vblank || !have)) begin
                h_total  <= reg_h_total[XB-1:0];
                h_active <= reg_h_active[XB-1:0];
                hs_beg   <= reg_hs_beg[XB-1:0];
                hs_end   <= reg_hs_end[XB-1:0];
                v_total  <= reg_v_total[YB-1:0];
                v_active <= reg_v_active[YB-1:0];
                vs_beg   <= reg_vs_beg[YB-1:0];
                vs_end   <= reg_vs_end[YB-1:0];
                hs_pos   <= reg_hs_pos;
                vs_pos   <= reg_vs_pos;
                ps_per_px <= reg_ps_per_px;
                win_x0 <= reg_win_x0; win_y0 <= reg_win_y0;
                win_w  <= reg_win_w;  win_h  <= reg_win_h;
                center <= reg_center;
                have    <= 1'b1;
                arm_ack <= 1'b1;
            end else if (!req_s1) arm_ack <= 1'b0;
        end
    end

    wire [15:0] act_x0, act_y0;
    raster #(.DW(DW), .XB(XB), .YB(YB), .FILL(FILL), .G_LEAD(G_LEAD)) core (
        .clk(clk), .rst(rst || !have), .locked(locked),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tuser(s_axis_tuser),
        .s_axis_tlast(s_axis_tlast),
        .h_total(h_total), .h_active(h_active),
        .hs_beg(hs_beg), .hs_end(hs_end),
        .v_total(v_total), .v_active(v_active),
        .vs_beg(vs_beg), .vs_end(vs_end),
        .hs_pos(hs_pos), .vs_pos(vs_pos), .ps_per_px(ps_per_px),
        .win_x0(win_x0), .win_y0(win_y0), .win_w(win_w), .win_h(win_h),
        .center(center), .follow(param_follow),
        .ps_en(ps_en), .ps_incdec(ps_incdec), .ps_done(ps_done),
        .act_x0(act_x0), .act_y0(act_y0),
        .vid_active_video(vid_active_video), .vid_data(vid_data),
        .vid_hsync(vid_hsync), .vid_vsync(vid_vsync),
        .status(status));
endmodule
