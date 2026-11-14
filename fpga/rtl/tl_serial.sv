// Serial TileLink (block 0x07): the FPGA's end of the DUT's 1-bit serial TileLink port.
//
// The DUT drives TL_CLK. Each direction is one bit wide with a ready/valid handshake, and a bit
// moves on a TL_CLK rising edge where its valid and ready are both high:
//   to the DUT:   TL_VAL_IN, TL_DAT_IN (ours)   and RDY_IN (the DUT's)
//   from the DUT: TL_VAL_OUT, TL_DAT_OUT (the DUT's) and RDY_OUT (ours)
// A frame is TL_FRAME bits, LSB first, with nothing between frames. The engine does not know
// what a frame holds: the host packs and unpacks the TileLink messages (scum.py), so a chip with
// another frame layout needs only TL_FRAME and the host changed.
//
// The engine runs on TL_CLK's falling edges, half way between the DUT's rising edges: it samples
// the DUT's outputs in the middle of their bit and changes its own half a period before the DUT
// samples them, which leaves half a period of margin both ways for the BUFG's insertion delay
// and the pads. At falling edge k+1/2 it knows, from registers, whether a bit moved at rising
// edge k (our valid/ready as driven, the DUT's as sampled at k-1/2), and it sets up edge k+1.
//
// The host side: words to send go into a FIFO (TL_TX), received words come out of another
// (TL_RX). Both FIFOs cross between the system clock and TL_CLK; their TL_CLK ends run on the
// rising edge, so the engine hands them words through pulse registers. TL_CTRL.EN off holds
// everything empty, and works without TL_CLK.
module tl_serial
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    input  logic        tick_ms,
    output logic        en,          // the engine owns the TileLink pads
    // the link, through a BUFG
    input  logic        tclk,
    output logic        val_to_dut,  // TL_VAL_IN
    output logic        dat_to_dut,  // TL_DAT_IN
    input  logic        rdy_dut,     // RDY_IN
    input  logic        val_dut,     // TL_VAL_OUT
    input  logic        dat_dut,     // TL_DAT_OUT
    output logic        rdy_to_dut   // RDY_OUT
);
    localparam int CW = $clog2(TL_FIFO) + 1;
    wire sel = bus_addr[15:8] == A_TL_CTRL[15:8];

    // ================= system clock: registers =================
    logic [7:0] frame_m1;   // TL_FRAME - 1; static while the engine runs
    logic arst = 1'b1;
    always_ff @(posedge clk) begin
        if (rst) begin
            en <= 1'b0;
            frame_m1 <= 8'(R_TL_FRAME - 1);
        end else if (bus_wr && sel) begin
            if (bus_addr == A_TL_CTRL) en <= bus_wdata[TL_CTRL_EN];
            if (bus_addr == A_TL_FRAME)
                frame_m1 <= bus_wdata == 0 ? 8'd0 : bus_wdata > 256 ? 8'd255 : 8'(bus_wdata - 1);
        end
        arst <= rst || !en;
    end

    // ================= the FIFOs =================
    logic [31:0] tx_word, rx_word, rx_head;
    logic tx_pop, tx_empty, rx_push, rx_empty;
    logic [CW-1:0] tx_wcount, rx_rcount, rx_wcount, tx_rcount_unused;
    async_fifo #(.W(32), .DEPTH(TL_FIFO)) u_txf (
        .arst, .wclk(clk), .wr(bus_wr && sel && bus_addr == A_TL_TX && en), .wdata(bus_wdata),
        .wfull(), .wcount(tx_wcount),
        .rclk(tclk), .rd(tx_pop), .rdata(tx_word), .rempty(tx_empty), .rcount(tx_rcount_unused)
    );
    wire rx_rd = bus_rd && sel && bus_addr == A_TL_RX;
    async_fifo #(.W(32), .DEPTH(TL_FIFO)) u_rxf (
        .arst, .wclk(tclk), .wr(rx_push), .wdata(rx_word), .wfull(), .wcount(rx_wcount),
        .rclk(clk), .rd(rx_rd), .rdata(rx_head), .rempty(rx_empty), .rcount(rx_rcount)
    );

    // ================= TL_CLK falling edges: the engine =================
    (* ASYNC_REG = "TRUE" *) logic [1:0] trst_s = '1;
    always_ff @(posedge tclk or posedge arst)
        if (arst) trst_s <= '1;
        else trst_s <= {trst_s[0], 1'b0};
    wire trst = trst_s[1];

    // to the DUT
    logic [31:0] tsr;          // the word being sent; bit 0 is on TL_DAT_IN
    logic [5:0]  tbits;        // bits of it still to send (0 = none loaded)
    logic [7:0]  tpos;         // bit position in the frame
    logic        rdy_dut_s;    // RDY_IN as the DUT drove it for the last rising edge
    // from the DUT
    logic [31:0] rsr;
    logic [4:0]  rbit;         // bit position in the word being received
    logic [7:0]  rpos;
    logic        val_dut_s, dat_dut_s;
    logic        active;       // a frame part sent, for TX_BUSY

    wire tx_moved = val_to_dut && rdy_dut_s;     // a bit went at the last rising edge
    wire rx_moved = val_dut_s && rdy_to_dut;      // a bit came
    wire [8:0] tleft = {1'b0, frame_m1} - {1'b0, tpos} + 1'b1;

    always_ff @(negedge tclk or posedge trst)
        if (trst) begin
            tsr <= '0;
            tbits <= '0;
            tpos <= '0;
            val_to_dut <= 1'b0;
            tx_pop <= 1'b0;
            rdy_dut_s <= 1'b0;
            rsr <= '0;
            rbit <= '0;
            rpos <= '0;
            rx_push <= 1'b0;
            rx_word <= '0;
            rdy_to_dut <= 1'b0;
            val_dut_s <= 1'b0;
            dat_dut_s <= 1'b0;
            active <= 1'b0;
        end else begin
            rdy_dut_s <= rdy_dut;
            val_dut_s <= val_dut;
            dat_dut_s <= dat_dut;

            // ---- send ----
            tx_pop <= 1'b0;
            if (tx_moved) begin
                tsr <= tsr >> 1;
                if (tpos == frame_m1 || tbits == 1) begin
                    tbits <= '0;
                    val_to_dut <= 1'b0;
                end else tbits <= tbits - 1'b1;
                tpos <= tpos == frame_m1 ? '0 : tpos + 1'b1;
            end else if (tbits == 0 && !tx_empty && !tx_pop) begin
                // the FIFO's head; tx_pop moves it on at the next rising edge
                tsr <= tx_word;
                tbits <= tleft > 32 ? 6'd32 : tleft[5:0];
                val_to_dut <= 1'b1;
                tx_pop <= 1'b1;
            end
            active <= tbits != 0 || tpos != 0;

            // ---- receive ----
            rx_push <= 1'b0;
            if (rx_moved) begin
                rsr[rbit] <= dat_dut_s;
                if (rbit == 31 || rpos == frame_m1) begin
                    rx_word <= rsr | (32'(dat_dut_s) << rbit);
                    rx_push <= 1'b1;
                    rsr <= '0;
                    rbit <= '0;
                end else rbit <= rbit + 1'b1;
                rpos <= rpos == frame_m1 ? '0 : rpos + 1'b1;
            end
            // ready while two words fit: the count lags our own pushes by a rising edge
            rdy_to_dut <= rx_wcount < CW'(TL_FIFO - 2);
        end
    assign dat_to_dut = tsr[0];

    // TL_CLK monitor: a Gray-coded cycle count for the system clock to difference every ms
    logic [15:0] tcnt = '0, tgray = '0;
    always_ff @(posedge tclk) begin
        tcnt <= tcnt + 1'b1;
        tgray <= tcnt ^ (tcnt >> 1);
    end

    // ================= system clock: status and reads =================
    (* ASYNC_REG = "TRUE" *) logic [15:0] g0, g1;
    logic [15:0] gbin, gprev, khz;
    always_comb begin
        gbin[15] = g1[15];
        for (int i = 14; i >= 0; i--) gbin[i] = gbin[i + 1] ^ g1[i];
    end
    logic active_s;
    sync2 u_active (.clk, .d(active), .q(active_s));

    always_ff @(posedge clk) begin
        g0 <= tgray;
        g1 <= g0;
        if (tick_ms) begin
            khz <= gbin - gprev;
            gprev <= gbin;
        end
    end

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_TL_CTRL:   rdata <= 32'(en);
                A_TL_FRAME:  rdata <= 32'(frame_m1) + 1;
                A_TL_RX:     rdata <= rx_empty ? '0 : rx_head;
                A_TL_STATUS: rdata <= {tx_wcount != 0 || active_s, 3'b0,
                                       12'(TL_FIFO - tx_wcount), 4'b0, 12'(rx_rcount)};
                A_TL_CLK_KHZ: rdata <= 32'(khz);
                default: ;
            endcase
    end
endmodule
