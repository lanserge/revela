// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_isp_axis_flow -- the adapter under a random source and a random sink:
// every beat offered leaves once and in order, a sink that never stalls
// is fed every cycle, a reset drops no beat already taken, and the
// source's ready is a register.
`timescale 1ns/1ps
module tb_isp_axis_flow;
    reg clk = 0, rst = 0;
    always #5 clk = !clk;
    reg in_valid = 0, in_sof = 0, in_eol = 0, in_last = 0;
    reg [23:0] in_data = 0;
    wire in_ready;
    wire m_valid, m_user, m_last;
    reg m_ready = 0;
    wire [23:0] m_data;
    wire [2:0] m_keep;
    isp_axis #(.DATA_BITS(24), .FB_WORD(0)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_ready(in_ready),
        .in_data(in_data), .in_sof(in_sof), .in_eol(in_eol),
        .in_last(in_last), .m_axis_tvalid(m_valid),
        .m_axis_tready(m_ready), .m_axis_tdata(m_data),
        .m_axis_tkeep(m_keep), .m_axis_tuser(m_user), .m_axis_tlast(m_last));

    integer fails = 0;
    task check(input ok, input [8*64-1:0] what);
        if (!ok) begin fails = fails + 1; $display("FAIL: %0s", what); end
    endtask

    // the beats are a count: R = G = B = n, sof on every 16th, eol on
    // every 8th, so order, loss and duplication all show in one number
    reg [7:0] sent = 0, got = 0;
    integer stalls = 0, starved = 0, took = 0;
    reg ready_moved = 0;
    reg [1:0] mode = 0;           // 0 random, 1 sink never stalls, 2 reset
    reg rdy_d = 0;
    always @(posedge clk) begin
        if (in_valid && in_ready) begin
            sent <= sent + 1'b1;
        end
        if (m_valid && m_ready) begin
            if (m_data !== {3{got}} || m_user !== (got[3:0] == 0)
                    || m_last !== (got[2:0] == 7)) begin
                fails = fails + 1;
                $display("FAIL: beat %0d left as %h sof %b eol %b",
                         got, m_data, m_user, m_last);
            end
            got <= got + 1'b1;
            took = took + 1;
        end
        if (mode == 1 && !(m_valid && m_ready)) starved = starved + 1;
    end
    // what the source offers next, held until taken
    always @(negedge clk) begin
        in_data <= {3{sent}};
        in_sof  <= sent[3:0] == 0;
        in_eol  <= sent[2:0] == 7;
        // ready is a register: it changes only just after an edge, never
        // with the sink's ready between edges
        rdy_d <= in_ready;
    end
    always @(m_ready) #1 if (in_ready !== rdy_d) ready_moved = 1;

    integer i;
    initial begin
        // random source, random sink
        for (i = 0; i < 4000; i = i + 1) begin
            @(negedge clk);
            if (!in_valid || in_ready) in_valid = ($random & 3) != 0;
            m_ready = ($random & 1);
        end
        // drain, then a sink that never stalls behind a source that
        // never starves: one beat a cycle
        in_valid = 0; m_ready = 1;
        repeat (8) @(negedge clk);
        check(sent == got, "every beat taken left, once");
        in_valid = 1; repeat (4) @(negedge clk);
        mode = 1; starved = 0;
        repeat (200) @(negedge clk);
        mode = 0;
        check(starved == 0, "a sink that never stalls is fed every cycle");
        // reset with beats held: none dropped, none new taken
        m_ready = 0; in_valid = 1;
        repeat (6) @(negedge clk);
        rst = 1; in_valid = 1;
        repeat (4) @(negedge clk);
        check(!in_ready, "reset takes no new beat");
        m_ready = 1;
        repeat (6) @(negedge clk);
        check(sent == got, "reset dropped no beat already taken");
        check(!m_valid, "and offers none it did not take");
        rst = 0; in_valid = 0;
        repeat (4) @(negedge clk);
        check(!ready_moved, "the source's ready is a register");
        if (fails == 0) $display("isp_axis: all checks passed");
        $finish;
    end
endmodule
