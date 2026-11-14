// Two-clock FIFO: Gray-coded pointers, each synchronised into the other side's clock.
//
// rdata shows the oldest word while !rempty (first-word fall-through); rd pops it. The levels are
// each side's own view, so they lag the other side by a few of its clocks: wcount can only
// overstate the fill and rcount understate it, which is the safe way round for both.
//
// arst empties the FIFO. It is asserted asynchronously, so it works without the other side's
// clock running, and each side leaves reset on its own clock.
module async_fifo #(
    parameter int W = 32,
    parameter int DEPTH = 512  // a power of two
) (
    input  logic                     arst,
    input  logic                     wclk,
    input  logic                     wr,
    input  logic [W-1:0]             wdata,
    output logic                     wfull,
    output logic [$clog2(DEPTH):0]   wcount,
    input  logic                     rclk,
    input  logic                     rd,
    output logic [W-1:0]             rdata,
    output logic                     rempty,
    output logic [$clog2(DEPTH):0]   rcount
);
    localparam int AW = $clog2(DEPTH);

    logic [W-1:0] mem [DEPTH];
    logic [AW:0] wbin = '0, wgray = '0, rbin = '0, rgray = '0;

    // per-side resets: asserted at once, released two clocks later on that side's clock
    (* ASYNC_REG = "TRUE" *) logic [1:0] wrst_s = '1, rrst_s = '1;
    always_ff @(posedge wclk or posedge arst)
        if (arst) wrst_s <= '1;
        else wrst_s <= {wrst_s[0], 1'b0};
    always_ff @(posedge rclk or posedge arst)
        if (arst) rrst_s <= '1;
        else rrst_s <= {rrst_s[0], 1'b0};
    wire wrst = wrst_s[1], rrst = rrst_s[1];

    function automatic logic [AW:0] gray(input logic [AW:0] b);
        return b ^ (b >> 1);
    endfunction
    function automatic logic [AW:0] ungray(input logic [AW:0] g);
        logic [AW:0] b;
        b[AW] = g[AW];
        for (int i = AW - 1; i >= 0; i--) b[i] = b[i + 1] ^ g[i];
        return b;
    endfunction

    // ---- write side ----
    (* ASYNC_REG = "TRUE" *) logic [AW:0] rg0 = '0, rg1 = '0;  // read pointer, Gray, synchronised
    wire [AW:0] rbin_w = ungray(rg1);
    assign wcount = wbin - rbin_w;
    assign wfull = wcount == (AW + 1)'(DEPTH);

    always_ff @(posedge wclk or posedge wrst)
        if (wrst) begin
            wbin <= '0;
            wgray <= '0;
            rg0 <= '0;
            rg1 <= '0;
        end else begin
            rg0 <= rgray;
            rg1 <= rg0;
            if (wr && !wfull) begin
                wbin <= wbin + 1'b1;
                wgray <= gray(wbin + 1'b1);
            end
        end

    always_ff @(posedge wclk)
        if (wr && !wfull) mem[wbin[AW-1:0]] <= wdata;

    // ---- read side ----
    (* ASYNC_REG = "TRUE" *) logic [AW:0] wg0 = '0, wg1 = '0;
    wire [AW:0] wbin_r = ungray(wg1);
    assign rcount = wbin_r - rbin;
    assign rempty = rcount == 0;
    assign rdata = mem[rbin[AW-1:0]];

    always_ff @(posedge rclk or posedge rrst)
        if (rrst) begin
            rbin <= '0;
            rgray <= '0;
            wg0 <= '0;
            wg1 <= '0;
        end else begin
            wg0 <= wgray;
            wg1 <= wg0;
            if (rd && !rempty) begin
                rbin <= rbin + 1'b1;
                rgray <= gray(rbin + 1'b1);
            end
        end
endmodule
