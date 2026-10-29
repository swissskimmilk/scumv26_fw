// Two-flop synchroniser for slow asynchronous inputs (pins, flags). Not for buses.
module sync2 #(
    parameter int W = 1,
    parameter logic [W-1:0] INIT = '0
) (
    input  logic         clk,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
    (* ASYNC_REG = "TRUE" *) logic [W-1:0] s0 = INIT, s1 = INIT;
    always_ff @(posedge clk) begin
        s0 <= d;
        s1 <= s0;
    end
    assign q = s1;
endmodule
