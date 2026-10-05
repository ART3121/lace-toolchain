// O circuito dos quatro fluxos do smoke: um flip-flop D.
module dff (
    input  clk,
    input  d,
    output reg q
);
  always @(posedge clk) q <= d;
endmodule
