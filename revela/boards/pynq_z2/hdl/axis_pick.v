// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two AXI-Stream sources, one sink: the scanout reads either the
// memory path (VDMA / fbread) or the ISP directly. `sel` is
// software's, quasi-static; the tee upstream switches frame-
// atomically and scanout re-anchors on tuser, so a flip here costs a
// frame, never a hang. The unselected source sees tready low: a
// stalled VDMA read side is the known, restartable kind of idle.
// Interfaces DECLARED, not inferred -- an inferred interface is a
// guess, and a guess about an AXI interface cost a day once.
module axis_pick (
    input  wire        sel,          // 0: A (memory), 1: B (direct)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 a TVALID" *)
    input  wire        a_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 a TREADY" *)
    output wire        a_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 a TDATA" *)
    input  wire [31:0] a_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 a TUSER" *)
    input  wire        a_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 a TLAST" *)
    input  wire        a_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 b TVALID" *)
    input  wire        b_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 b TREADY" *)
    output wire        b_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 b TDATA" *)
    input  wire [31:0] b_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 b TUSER" *)
    input  wire        b_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 b TLAST" *)
    input  wire        b_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m TVALID" *)
    output wire        m_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m TREADY" *)
    input  wire        m_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m TDATA" *)
    output wire [31:0] m_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m TUSER" *)
    output wire        m_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m TLAST" *)
    output wire        m_tlast
);
    assign m_tvalid = sel ? b_tvalid : a_tvalid;
    assign m_tdata  = sel ? b_tdata  : a_tdata;
    assign m_tuser  = sel ? b_tuser  : a_tuser;
    assign m_tlast  = sel ? b_tlast  : a_tlast;
    assign a_tready = !sel && m_tready;
    assign b_tready =  sel && m_tready;
endmodule
