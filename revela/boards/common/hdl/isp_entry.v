// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// The ISP's entry on this board: each frame's header facts latched as its
// SOF enters, and every line cropped to the width the ISP was built for.
// The ISP itself is generated and verified by revela from its composite;
// this only stands in front of it, so the design wires the two together
// and the ISP's widths reach this block as parameters, never as literals.
module isp_entry #(
    // One sample of the stream into the ISP: the depth its input port
    // declares, which the receiver aligns every source to.
    parameter SAMPLE_BITS = 12,
    // The widest line the ISP was BUILT for -- its line buffers are this
    // deep and its pointwise cores reframe every line from start-of-frame
    // plus this number. They receive neither the stream's end-of-line nor
    // the header's width, so a longer line cannot be noticed inside the
    // ISP: the cores would simply declare a new row part-way through the
    // sensor's, shearing the picture and inverting the CFA phase on
    // alternate rows, with colour applied to the wrong channels and no
    // status bit anywhere.
    //
    // The receiver upstream refuses only what ITS OWN buffers cannot hold
    // (max_line_bytes), which is a different and larger number -- 3276
    // samples at 10-bit against this 1920 -- so everything in between
    // arrives here perfectly well formed. Guarding it is this block's
    // job, because it is the only thing that knows both the line boundary
    // and what the ISP was built for.
    parameter WIDTH       = 1920
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK", X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF in_stream:out_stream, ASSOCIATED_RESET rst" *)
    input  wire        clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst RST", X_INTERFACE_PARAMETER = "POLARITY ACTIVE_HIGH" *)
    input  wire        rst,
    // The header's facts, straight from the receiver, latched as each
    // frame's SOF enters so a change lands on a frame boundary. NOT the
    // depth: the receiver has already aligned samples to this build's,
    // so hdr_bits is carried for software only.
    input  wire [15:0] hdr_width,
    input  wire [15:0] hdr_height,
    input  wire [1:0]  hdr_phase,
    input  wire [4:0]  hdr_bits,
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
    output wire [SAMPLE_BITS-1:0] out_data,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream SOF" *)
    output wire        out_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream EOL" *)
    output wire        out_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 out_stream LAST" *)
    output wire        out_last,
    // The frame's geometry and phase as the ISP is to be told them: its
    // stream context, held for the frame.
    output reg  [15:0] ctx_width = WIDTH,
    output reg  [15:0] ctx_height = 16'd0,
    output reg  [1:0]  ctx_bayer_phase = 2'd0,
    output reg  [4:0]  ctx_bit_depth = 5'd0,
    // Sticky: a line arrived wider than the ISP was built for and was
    // cropped to it. VISIBLE and stated, rather than refused into a dark
    // screen -- a picture that is merely narrow can be recognised and
    // tuned; a black one is indistinguishable from a dead cable.
    output reg         truncated
);
    always @(posedge clk)
        if (in_valid && in_ready && in_sof) begin
            // The ISP cannot process more than it was built for, so the
            // geometry it is TOLD is clamped to that. Everything
            // downstream stays self-consistent: the window, the phase and
            // the line buffers all agree on the same width.
            ctx_width       <= (hdr_width > WIDTH) ? WIDTH[15:0] : hdr_width;
            ctx_height      <= hdr_height;
            ctx_bayer_phase <= hdr_phase;
            ctx_bit_depth   <= hdr_bits;
        end

    // CROP, not refuse. Count this line's samples; hand the first WIDTH of
    // them to the ISP and swallow the rest until the stream's own
    // end-of-line. The ISP therefore sees exactly the lines it was built
    // for, with the CFA phase intact, and the picture is the left WIDTH
    // of a wider frame -- narrower than expected, and obviously so.
    reg [15:0] lcol = 16'd0;
    reg [15:0] lrow = 16'd0;
    wire       keep = (lcol < WIDTH);
    // The framing flags are RE-TIMED, not just gated. On a cropped line
    // the stream's own end-of-line and end-of-frame land on samples the
    // ISP never sees, and end-of-frame is not decoration: the ISP commits
    // a batch of coefficients on it. Gating it away would leave every
    // calibration write stranded in the shadow, on exactly the frames
    // where something is already going wrong. So the last KEPT sample of
    // each line carries the line end, and the last kept sample of the
    // last line carries the frame end.
    wire       line_end = keep && ((lcol == WIDTH - 16'd1)
                                   || in_eol || in_last);
    wire       frame_end = line_end && (lrow == ctx_height - 16'd1);
    // A dropped sample is ACCEPTED, not stalled: the surplus has to leave
    // the receiver or the line never reaches its end and the frame stops.
    // Only the kept samples wait on the ISP.
    assign in_ready  = keep ? out_ready : 1'b1;
    assign out_valid = in_valid && keep;
    assign out_data  = in_data;
    assign out_sof   = in_sof && keep;
    assign out_eol   = line_end;
    assign out_last  = frame_end;
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
endmodule
