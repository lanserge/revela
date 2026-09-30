// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// This board's shim around the revela design pack: the header latch and
// the AXI4-Lite port carried to the boundary, nothing else. The pack
// itself (revela_isp_core.v, in the build directory) is generated and
// verified by revela from pipeline.json; this file is hand-shaped
// board glue, like tee_shim.v. The literals below -- the sample width,
// the reset geometry, the core's name -- restate that design's facts, so
// the two files change together or not at all.
//
// Parameters are LIVE: written over the register map, landing in a
// shadow, committed at the frame boundary, so a frame is processed with
// one coherent set or none of it. NEUTRAL at power-on -- calibration is
// a sensor fact, restored over the cable by whoever holds the sensor's
// profile.
module revela_isp #(
    // BOTH of the ISP's boundary widths, handed down by the block design
    // from what the generator published. The ISP is the deliverable and
    // this shim is part of the harness around it, so it may not state
    // either number: the input was written here as [9:0] and stayed
    // correct exactly until the pipeline moved to 12 bits, at which
    // point a block design connects the low ten and calls it a warning.
    //
    // The NAMES are the ones the rest of the datapath already uses --
    // SAMPLE_BITS for one sample on the wire, DATA_BITS for the
    // pipeline word -- because a second name for a fact is a second
    // place to change it. The stream into the ISP is one sample per
    // beat, so its input width IS the sample width that rx_axis and
    // axis_unpack are built at; it was briefly carried alongside them
    // under a name of its own, which made two numbers out of one and
    // then wanted a check to keep them equal.
    parameter SAMPLE_BITS = 12,
    parameter DATA_BITS   = 24,
    // The widest line the ISP was BUILT for -- its line buffers are
    // this deep and its pointwise cores reframe every line from
    // start-of-frame plus this number. They receive neither the
    // stream's end-of-line nor the header's width, so a longer line
    // cannot be noticed inside the pack: the cores would simply
    // declare a new row part-way through the sensor's, shearing the
    // picture and inverting the CFA phase on alternate rows, with
    // colour applied to the wrong channels and no status bit anywhere.
    //
    // The receiver upstream refuses only what ITS OWN buffers cannot
    // hold (max_line_bytes), which is a different and larger number --
    // 3276 samples at 10-bit against this 1920 -- so everything in
    // between arrives here perfectly well formed. Guarding it is this
    // shim's job, because the shim is the only thing that knows both
    // the line boundary and what the pack was built for.
    parameter WIDTH    = 1920
) (
    input  wire        clk,
    input  wire        rst,
    // The header's facts, straight from the receiver, latched as each
    // frame's SOF enters so a change lands on a frame boundary. NOT the
    // depth: the receiver has already aligned samples to this build's,
    // so hdr_bits is carried for software only.
    input  wire [15:0] hdr_width,
    input  wire [15:0] hdr_height,
    input  wire [1:0]  hdr_phase,
    input  wire [4:0]  hdr_bits,
    // Every coefficient, and every identity word, behind this port. The
    // interface is DECLARED, not left to be inferred from port names: an
    // inferred one is a guess, and a guess about an AXI interface cost a
    // day on 2026-08-17.
    //
    // ON ITS OWN CLOCK. `clk` above is recovered from the HDMI link, so
    // it STOPS whenever the link is down -- including just after this
    // bitstream is loaded. AXI has no timeout, so a slave with no clock
    // never answers and the processor waits for it forever. That hung
    // this board on 2026-08-17 and needed the power pulled. The
    // association below is what tells the block design which clock this
    // port runs on, and naming the wrong one is exactly how it ended up
    // on a clock that can stop.
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET s_axi_aresetn" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    input  wire        s_axi_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR" *)
    input  wire [14:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input  wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output wire [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input  wire [14:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output wire [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input  wire        s_axi_rready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream VALID" *)
    input  wire        in_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream READY" *)
    output wire        in_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream DATA" *)
    input  wire [SAMPLE_BITS-1:0] in_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream SOF" *)
    input  wire        in_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream EOL" *)
    input  wire        in_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream LAST" *)
    input  wire        in_last,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream VALID" *)
    output wire        out_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream READY" *)
    input  wire        out_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream DATA" *)
    output wire [DATA_BITS-1:0] out_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream SOF" *)
    output wire        out_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream EOL" *)
    output wire        out_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream LAST" *)
    output wire        out_last,
    // Sticky: a line arrived wider than the pack was built for and was
    // cropped to it. VISIBLE and stated, rather than refused into a
    // dark screen -- a picture that is merely narrow can be recognised
    // and tuned; a black one is indistinguishable from a dead cable.
    output reg         truncated
);
    reg [15:0] ctx_w = 16'd1920;
    reg [15:0] ctx_h = 16'd1080;
    reg [1:0]  ctx_ph = 2'd2;
    reg [4:0]  ctx_bd = 5'd10;
    always @(posedge clk)
        if (in_valid && in_ready && in_sof) begin
            // The pack cannot process more than it was built for, so
            // the geometry it is TOLD is clamped to that. Everything
            // downstream stays self-consistent: the window, the phase
            // and the line buffers all agree on the same width.
            ctx_w  <= (hdr_width > WIDTH) ? WIDTH[15:0] : hdr_width;
            ctx_h  <= hdr_height;
            ctx_ph <= hdr_phase;
            ctx_bd <= hdr_bits;
        end

    // CROP, not refuse. Count this line's samples; hand the first
    // WIDTH of them to the pack and swallow the rest until the
    // stream's own end-of-line. The pack therefore sees exactly the
    // lines it was built for, with the CFA phase intact, and the
    // picture is the left WIDTH of a wider frame -- narrower than
    // expected, and obviously so.
    reg [15:0] lcol = 16'd0;
    reg [15:0] lrow = 16'd0;
    wire       keep = (lcol < WIDTH);
    wire       core_ready;
    // The framing flags are RE-TIMED, not just gated. On a cropped line
    // the stream's own end-of-line and end-of-frame land on samples the
    // pack never sees, and end-of-frame is not decoration: the pack
    // commits a batch of coefficients on it. Gating it away would leave
    // every calibration write stranded in the shadow, on exactly the
    // frames where something is already going wrong. So the last KEPT
    // sample of each line carries the line end, and the last kept
    // sample of the last line carries the frame end.
    wire       line_end = keep && ((lcol == WIDTH - 16'd1)
                                   || in_eol || in_last);
    wire       frame_end = line_end && (lrow == ctx_h - 16'd1);
    // A dropped sample is ACCEPTED, not stalled: the surplus has to
    // leave the receiver or the line never reaches its end and the
    // frame stops. Only the kept samples wait on the pack.
    assign in_ready = keep ? core_ready : 1'b1;
    always @(posedge clk) begin
        if (rst) begin
            lcol <= 16'd0;
            truncated <= 1'b0;          // sticky across frames, not resets
        end else if (in_valid && in_ready) begin
            if (in_eol || in_last) begin
                lcol <= 16'd0;
                lrow <= in_last ? 16'd0 : (lrow + 16'd1);
            end else lcol <= lcol + 16'd1;
            if (in_sof) lrow <= 16'd0;
            if (!keep) truncated <= 1'b1;
        end
    end
    revela_isp_core_ctrl core (
        .clk(clk), .rst(rst),
        .s_axil_aclk(s_axi_aclk), .s_axil_aresetn(s_axi_aresetn),
        .s_axil_awaddr(s_axi_awaddr), .s_axil_awvalid(s_axi_awvalid),
        .s_axil_awready(s_axi_awready),
        .s_axil_wdata(s_axi_wdata), .s_axil_wstrb(s_axi_wstrb),
        .s_axil_wvalid(s_axi_wvalid), .s_axil_wready(s_axi_wready),
        .s_axil_bresp(s_axi_bresp), .s_axil_bvalid(s_axi_bvalid),
        .s_axil_bready(s_axi_bready),
        .s_axil_araddr(s_axi_araddr), .s_axil_arvalid(s_axi_arvalid),
        .s_axil_arready(s_axi_arready),
        .s_axil_rdata(s_axi_rdata), .s_axil_rresp(s_axi_rresp),
        .s_axil_rvalid(s_axi_rvalid), .s_axil_rready(s_axi_rready),
        .ctx_width(ctx_w), .ctx_height(ctx_h),
        .ctx_bayer_phase(ctx_ph), .ctx_bit_depth(ctx_bd),
        .isp_in_valid(in_valid && keep), .isp_in_ready(core_ready),
        .isp_in_data(in_data),
        .isp_in_sof(in_sof && keep), .isp_in_eol(line_end),
        .isp_in_last(frame_end),
        .isp_out_valid(out_valid), .isp_out_ready(out_ready),
        .isp_out_data(out_data),
        .isp_out_sof(out_sof), .isp_out_eol(out_eol),
        .isp_out_last(out_last)
    );
endmodule
