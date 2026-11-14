// DUT UART (block 0x08): FPGA_UART_TX / FPGA_UART_RX, 8N1 at SYS_HZ / UART_DIV baud, with a FIFO
// each way that the host fills and drains with register writes and burst reads.
//
// The receiver waits for a falling edge, checks the start bit half a bit later, then samples
// each data bit and the stop bit in its middle. A low stop bit sets UART_EVENTS.FRAMING (the byte
// is kept); a byte that finds the receive FIFO full is lost and sets OVERRUN.
//
// TX is released and RX ignored while the DUT's VIO is not up, as for the other DUT pads.
module uart
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    input  logic        vio_on,
    output logic        tx,
    output logic        tx_oe,
    input  logic        rx_pin
);
    localparam int RW = $clog2(UART_RX_FIFO), TW = $clog2(UART_TX_FIFO);
    wire sel = bus_addr[15:8] == A_UART_DIV[15:8];
    wire wr = bus_wr && sel;

    logic [15:0] div;
    logic overrun, framing;

    // ---- FIFOs (one clock) ----
    logic [7:0] rmem [UART_RX_FIFO];
    logic [7:0] tmem [UART_TX_FIFO];
    logic [RW:0] rw_ptr, rr_ptr;
    logic [TW:0] tw_ptr, tr_ptr;
    wire [RW:0] rcount = rw_ptr - rr_ptr;
    wire [TW:0] tcount = tw_ptr - tr_ptr;

    // ---- receiver ----
    logic rx_s;
    sync2 #(.INIT(1'b1)) u_rx (.clk, .d(rx_pin), .q(rx_s));
    wire rx_in = vio_on ? rx_s : 1'b1;
    logic [3:0] rstate;          // 0 idle, 1 start, 2-9 data, 10 stop
    logic [15:0] rcnt;
    logic [7:0] rsr;
    logic rpush;

    // ---- transmitter ----
    logic [3:0] tstate;          // 0 idle, 1 start, 2-9 data, 10 stop
    logic [15:0] tcnt;
    logic [7:0] tsr;

    always_ff @(posedge clk) begin
        rpush <= 1'b0;
        if (rst) begin
            div <= 16'(R_UART_DIV);
            overrun <= 1'b0;
            framing <= 1'b0;
            rw_ptr <= '0;
            rr_ptr <= '0;
            tw_ptr <= '0;
            tr_ptr <= '0;
            rstate <= '0;
            tstate <= '0;
            tx <= 1'b1;
        end else begin
            if (wr && bus_addr == A_UART_DIV) div <= bus_wdata[15:0] < 16 ? 16'd16 : bus_wdata[15:0];
            if (wr && bus_addr == A_UART_EVENTS) begin
                if (bus_wdata[UART_EVENTS_OVERRUN]) overrun <= 1'b0;
                if (bus_wdata[UART_EVENTS_FRAMING]) framing <= 1'b0;
            end
            if (wr && bus_addr == A_UART_TX && tcount != (TW + 1)'(UART_TX_FIFO)) begin
                tmem[tw_ptr[TW-1:0]] <= bus_wdata[7:0];
                tw_ptr <= tw_ptr + 1'b1;
            end
            if (bus_rd && sel && bus_addr == A_UART_RX && rcount != 0) rr_ptr <= rr_ptr + 1'b1;

            // receive
            case (rstate)
                0: if (!rx_in) begin
                    rstate <= 1;
                    rcnt <= div >> 1;
                end
                default:
                    if (rcnt != 0) rcnt <= rcnt - 1'b1;
                    else begin
                        rcnt <= div - 1'b1;
                        if (rstate == 1) rstate <= rx_in ? 4'd0 : 4'd2;  // a glitch, not a start
                        else if (rstate <= 9) begin
                            rsr <= {rx_in, rsr[7:1]};
                            rstate <= rstate + 1'b1;
                        end else begin
                            if (!rx_in) framing <= 1'b1;
                            rpush <= 1'b1;
                            rstate <= '0;
                        end
                    end
            endcase
            if (rpush) begin
                if (rcount == (RW + 1)'(UART_RX_FIFO)) overrun <= 1'b1;
                else begin
                    rmem[rw_ptr[RW-1:0]] <= rsr;
                    rw_ptr <= rw_ptr + 1'b1;
                end
            end

            // transmit
            if (tstate == 0) begin
                if (tcount != 0) begin
                    tsr <= tmem[tr_ptr[TW-1:0]];
                    tr_ptr <= tr_ptr + 1'b1;
                    tx <= 1'b0;
                    tcnt <= div - 1'b1;
                    tstate <= 1;
                end
            end else if (tcnt != 0) tcnt <= tcnt - 1'b1;
            else begin
                tcnt <= div - 1'b1;
                if (tstate <= 8) begin
                    tx <= tsr[0];
                    tsr <= tsr >> 1;
                    tstate <= tstate + 1'b1;
                end else if (tstate == 9) begin
                    tx <= 1'b1;  // stop bit
                    tstate <= 10;
                end else tstate <= 0;
            end
        end
    end
    assign tx_oe = vio_on;

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_UART_DIV:    rdata <= 32'(div);
                A_UART_RX:     rdata <= rcount != 0 ? {23'b0, 1'b1, rmem[rr_ptr[RW-1:0]]} : '0;
                A_UART_STATUS: rdata <= {4'b0, 12'(tcount), 4'b0, 12'(rcount)};
                A_UART_EVENTS: rdata <= {30'b0, framing, overrun};
                default: ;
            endcase
    end
endmodule
