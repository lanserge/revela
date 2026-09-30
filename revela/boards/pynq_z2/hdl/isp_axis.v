// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// The ISP's RGB stream, dressed as AXI4-Stream video for a VDMA S2MM
// writing the framebuffer. Channel 0 rides the LOW bits of the pipeline
// word (revela's packing law); the framebuffer byte order comes from
// rgb2dvi's bus: [23:16] R, [15:8] B, [7:0] G -- via the little-endian
// framebuffer word, bytes G, B, R, pad. tuser marks start of frame,
// tlast end of line: the VDMA's dialect.
//
// DATA_BITS is the width the ISP actually TRACED, passed in by the block
// design from what the generator published. It was written down here as
// 30 while the pipeline was 10-bit, and that is a number which stays
// correct until the day the pipeline changes and then produces a picture
// rather than an error -- the slices would still elaborate against a
// wider word and quietly read the wrong bits.
module isp_axis #(
    parameter DATA_BITS = 24
) (
    input  wire        clk,
    input  wire        rst,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream VALID" *)
    input  wire        in_valid,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream READY" *)
    output wire        in_ready,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream DATA" *)
    input  wire [DATA_BITS-1:0] in_data,   // R low, G mid, B high
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream SOF" *)
    input  wire        in_sof,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream EOL" *)
    input  wire        in_eol,
    (* X_INTERFACE_INFO = "lanserge:interface:np2hw_stream_rtl:1.0 in_stream LAST" *)
    input  wire        in_last,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire [31:0] m_axis_tdata,
    output wire [3:0]  m_axis_tkeep,
    output wire        m_axis_tuser,
    output wire        m_axis_tlast
);
    // Three equal lanes, and the cable downstream is 8 bits a channel
    // whatever the pipeline carries, so this takes each lane's TOP eight.
    // With the display curve doing the narrowing (the ordinary case) a
    // lane is already 8 and this is the identity; a design that keeps
    // more depth to the end truncates once, here, at the cable.
    localparam CH = DATA_BITS / 3;
    wire [7:0] r8 = in_data[0*CH + CH-1 -: 8];
    wire [7:0] g8 = in_data[1*CH + CH-1 -: 8];
    wire [7:0] b8 = in_data[2*CH + CH-1 -: 8];
`ifdef SIMULATION
    initial if (CH * 3 !== DATA_BITS || CH < 8)
        $fatal(1, "isp_axis: DATA_BITS=%0d is not three lanes of at least 8",
               DATA_BITS);
`endif

    // A FENCE, not a wire -- because this module's reset and its
    // consumer's are different laws. The ISP island resets when the
    // LINK drops; the VDMA on the far side keeps its clock and its
    // reset and remembers every promise made to it. The combinational
    // version passed tvalid straight through, so the island reset
    // CHOPPED a transfer mid-beat -- an AXI-Stream protocol violation
    // -- and a DataMover holding a violated handshake wedges, taking
    // its register interface with it and, since AXI has no timeout,
    // the first software access after that, and the processor. Pulled
    // cable, frozen TV, dead board: measured on 2026-08-18.
    //
    // So the output is a register, and reset does NOT clear it: a beat
    // already offered stays offered, stable, until the sink takes it.
    // Reset only stops NEW beats entering. The sink sees a clean
    // starve -- at worst the restartable frame-count halt, never a
    // violation. Power-on state comes from the initial values (GSR),
    // which is exactly the one moment nothing can be in flight.
    reg        v_q = 1'b0;
    reg [31:0] d_q = 32'h0;
    reg        u_q = 1'b0, l_q = 1'b0;

    assign m_axis_tvalid = v_q;
    assign m_axis_tdata  = d_q;
    assign m_axis_tuser  = u_q;
    assign m_axis_tlast  = l_q;
    assign m_axis_tkeep  = 4'b1111;
    assign in_ready      = !rst && (!v_q || m_axis_tready);

    always @(posedge clk) begin
        if (v_q && m_axis_tready)
            v_q <= 1'b0;
        if (!rst && in_valid && (!v_q || m_axis_tready)) begin
            v_q <= 1'b1;
            d_q <= {8'h00, r8, b8, g8};
            u_q <= in_sof;
            l_q <= in_eol;
        end
    end
endmodule
