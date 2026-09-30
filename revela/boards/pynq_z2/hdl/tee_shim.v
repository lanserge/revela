// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// The ISP's stream, split for two consumers: the store (VDMA) and the
// direct display branch. The tee itself is np2hw's, payload-agnostic;
// this shim owns ONE fact -- how the flags pack beside the pixels --
// stated here once so both branches unpack the same layout.
//   [DATA_BITS-1:0] rgb   [+0] eol   [+1] last   [+2] sof
//
// DATA_BITS is the width the ISP traced, handed down by the block design
// rather than written here: this shim owns the LAYOUT, not the width.
module tee_shim #(
    parameter DATA_BITS = 24
) (
    input  wire        clk,
    input  wire        rst,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream VALID" *)
    input  wire        in_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream READY" *)
    output wire        in_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream DATA" *)
    input  wire [DATA_BITS-1:0] in_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream SOF" *)
    input  wire        in_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream EOL" *)
    input  wire        in_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream LAST" *)
    input  wire        in_last,
    input  wire        en_a,       // direct display branch
    input  wire        en_b,       // framebuffer store branch
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a VALID" *)
    output wire        a_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a READY" *)
    input  wire        a_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a DATA" *)
    output wire [DATA_BITS-1:0] a_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a SOF" *)
    output wire        a_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a EOL" *)
    output wire        a_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 a LAST" *)
    output wire        a_last,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b VALID" *)
    output wire        b_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b READY" *)
    input  wire        b_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b DATA" *)
    output wire [DATA_BITS-1:0] b_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b SOF" *)
    output wire        b_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b EOL" *)
    output wire        b_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 b LAST" *)
    output wire        b_last,
    // sticky until reset: that branch lost mid-frame beats -- the
    // tee never stalls, so a consumer that falls behind tears its
    // own branch and testifies here
    output wire        torn_a,
    output wire        torn_b
);
    // Every width below is DATA_BITS + 3: the ISP's traced word plus
    // the three flags. The skid was instantiated at a literal 33 and
    // the FIFO took its default of the same, which is a 30-bit RGB
    // from when the pipeline was 10-bit. Nothing failed, because the
    // payload sits in the low bits and the extra ones zero-extend and
    // truncate back -- so it read as working while carrying six dead
    // bits through a 4096-deep block RAM, and it would have cut the
    // flags off the top the first time an ISP traced wider than 30.
    wire [DATA_BITS+2:0] packed_in = {in_sof, in_last, in_eol, in_data};
    wire [DATA_BITS+2:0] a_packed, b_packed;

    // The skid FIRST: the ISP's in_ready ripples combinationally from
    // its sink through every block (the boundary_report finding), and
    // the tee's lockstep fork below would add its own levels to that
    // cone. The skid's registered ready is where the cone ends -- the
    // ISP sees a flop whatever is wired past this point.
    wire [DATA_BITS+2:0] sk_data;
    wire        sk_valid, sk_ready;
    isp_skid #(.W(DATA_BITS + 3)) u_skid (
        .clk(clk), .rst(rst),
        .s_data(packed_in), .s_valid(in_valid), .s_ready(in_ready),
        .m_data(sk_data), .m_valid(sk_valid), .m_ready(sk_ready));

    wire        bt_valid, bt_ready;
    isp_tee #(.W(DATA_BITS + 3)) u_tee (
        .clk(clk), .rst(rst),
        .in_valid(sk_valid), .in_ready(sk_ready),
        .in_data(sk_data), .in_sof(sk_data[DATA_BITS + 2]),
        .en_a(en_a), .en_b(en_b),
        .a_valid(a_valid), .a_ready(a_ready), .a_data(a_packed),
        .b_valid(bt_valid), .b_ready(bt_ready), .b_data(b_packed),
        .torn_a(torn_a), .torn_b(torn_b));

    assign {a_sof, a_last, a_eol, a_data} = a_packed;

    // The grabber's elastic, INSIDE the tap: the tee never stalls,
    // so branch B's ready at the tee must be as smooth as the
    // stream itself. The write engine's ready dips during bursts;
    // two lines of slack here absorbs them, exactly as the display
    // branch's crossing FIFO absorbs its side. Without this, every
    // dip tears the branch (bench-paid: DMAIntErr on every frame,
    // a sentinel buffer never written).
    wire [DATA_BITS+2:0] bq_data;
    grab_fifo #(.W(DATA_BITS + 3)) u_bfifo (
        .wclk(clk), .wrst(rst),
        .in_data(b_packed), .in_valid(bt_valid), .in_ready(bt_ready),
        .rclk(clk), .rrst(rst),
        .out_data(bq_data), .out_valid(b_valid),
        .out_ready(b_ready));
    assign {b_sof, b_last, b_eol, b_data} = bq_data;
endmodule
