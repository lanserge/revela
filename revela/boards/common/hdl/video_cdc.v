// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// video_cdc -- a raster into a transmitter's own pixel clock: the same
// frequency, an unknown phase. The transmitter's pixel clock is a
// regional divider of its bit clock, and which of the five bit-clock
// edges the divider starts on after a relock is chance -- so a raster
// register sampled straight in that clock is sampled anywhere in its
// cycle: at 27 MHz there is room for any of the five, at 74.25 and
// 148.5 MHz there is not, and the TV sees no signal.
//
// So the raster goes through a short ring instead. The two clocks are one
// frequency -- the transmitter's clock manager is locked to the raster's
// clock -- so no flow control is needed: the write side stores a beat
// every cycle, the read side starts reading LAG entries behind where the
// write side has been seen to be and then reads a beat every cycle, and
// the distance between them holds whatever the phase. The memory is read
// LAG entries old, so nothing read is changing; the clocks are asynchronous
// to the timer.
module video_cdc #(
    parameter DW = 24,
    parameter LAG = 4                   // entries between writer and reader
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 wclk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF vid_in" *)
    input  wire          wclk,          // the raster's clock
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_in ACTIVE_VIDEO" *)
    input  wire          vid_in_active_video,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_in DATA" *)
    input  wire [DW-1:0] vid_in_data,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_in HSYNC" *)
    input  wire          vid_in_hsync,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_in VSYNC" *)
    input  wire          vid_in_vsync,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 rclk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF vid_out, ASSOCIATED_RESET rrst" *)
    input  wire          rclk,          // the transmitter's pixel clock
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rrst RST", X_INTERFACE_PARAMETER = "POLARITY ACTIVE_HIGH" *)
    input  wire          rrst,          // asynchronous: the transmitter's
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_out ACTIVE_VIDEO" *)
    output reg           vid_out_active_video,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_out DATA" *)
    output reg  [DW-1:0] vid_out_data,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_out HSYNC" *)
    output reg           vid_out_hsync,
    (* X_INTERFACE_INFO = "xilinx.com:interface:vid_io:1.0 vid_out VSYNC" *)
    output reg           vid_out_vsync
);
    localparam AB = 4;                  // sixteen entries
    localparam W = DW + 3;

    // the writer: every raster cycle, free running
    (* ram_style = "distributed" *) reg [W-1:0] mem [0:(1<<AB)-1];
    reg [AB-1:0] wbin = 0, wgray = 0;
    always @(posedge wclk) begin
        mem[wbin] <= {vid_in_active_video, vid_in_hsync, vid_in_vsync,
                      vid_in_data};
        wbin <= wbin + 1'b1;
        wgray <= (wbin + 1'b1) ^ ((wbin + 1'b1) >> 1);
    end

    // where the writer has been seen to be, in the reader's clock
    (* ASYNC_REG = "TRUE" *) reg [AB-1:0] wg0 = 0, wg1 = 0;
    always @(posedge rclk) begin
        wg0 <= wgray;
        wg1 <= wg0;
    end
    reg [AB-1:0] wseen;
    integer i;
    always @* begin
        wseen[AB-1] = wg1[AB-1];
        for (i = AB - 2; i >= 0; i = i - 1)
            wseen[i] = wseen[i+1] ^ wg1[i];
    end

    // the reader: placed LAG behind on reset, then one entry a cycle
    (* ASYNC_REG = "TRUE" *) reg rs0 = 1'b1, rs1 = 1'b1;
    always @(posedge rclk or posedge rrst)
        if (rrst) {rs0, rs1} <= 2'b11;
        else      {rs0, rs1} <= {1'b0, rs0};
    reg [AB-1:0] rbin = 0;
    reg [W-1:0] q;
    always @(posedge rclk) begin
        rbin <= rs1 ? wseen - LAG[AB-1:0] : rbin + 1'b1;
        q <= mem[rbin];
        {vid_out_active_video, vid_out_hsync, vid_out_vsync, vid_out_data}
            <= rs1 ? {3'b000, {DW{1'b0}}} : q;
    end
endmodule
