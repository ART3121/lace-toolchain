// Testbench em Verilog, para o Icarus e o Verilator sem cocotb.
`timescale 1ns / 1ps
module dff_tb;
  reg clk = 0, d = 0;
  wire q;
  dff dut (.clk(clk), .d(d), .q(q));
  always #5 clk = ~clk;
  initial begin
    d = 1;
    #10;
    if (q !== 1) $display("SMOKE_FAIL q=%b", q);
    else $display("SMOKE_OK");
    $finish;
  end
endmodule
