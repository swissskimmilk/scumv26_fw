// FT2232H channel B in asynchronous 245 FIFO mode (the EEPROM sets channel B to "245 FIFO").
//
// The FT2232H has no clock in this mode: RXF# low = a byte from the PC is waiting, TXE# low =
// there is room for a byte to the PC. We strobe RD# or WR# with fixed widths counted in system
// clock cycles. The defaults (100 MHz) hold every strobe and setup about twice the FT2232H
// datasheet minimum (RD#/WR# active >= 30 ns, precharge >= 50 ns, RD# to data <= 14 ns):
// about 6 MB/s each way.
//
// One byte at a time, and a byte to the PC goes first when both are possible, so a reply is
// never held up behind the PC's next command.
module ft245_async #(
    parameter int RD_LOW = 6,   // RD# low; data is sampled in its last cycle
    parameter int WR_SETUP = 2, // data on the bus before WR# falls
    parameter int WR_LOW = 6,   // WR# low
    parameter int WR_HOLD = 2,  // data held after WR# rises
    parameter int RECOVER = 8   // between strobes (covers RXF#/TXE# update + synchroniser)
) (
    input  logic       clk,
    input  logic       rst,
    // pins
    input  logic       rxf_n,
    input  logic       txe_n,
    output logic       rd_n,
    output logic       wr_n,
    input  logic [7:0] d_in,
    output logic [7:0] d_out,
    output logic       d_oe,
    // from the PC
    output logic [7:0] rx_data,
    output logic       rx_valid,
    input  logic       rx_ready,
    // to the PC
    input  logic [7:0] tx_data,
    input  logic       tx_valid,
    output logic       tx_ready
);
    logic rxf_s, txe_s;
    sync2 #(.W(2), .INIT(2'b11)) u_sync (.clk, .d({rxf_n, txe_n}), .q({rxf_s, txe_s}));

    typedef enum logic [2:0] {IDLE, RD, WR_S, WR_L, WR_H, REC} state_t;
    state_t st;
    logic [3:0] n;

    assign tx_ready = (st == IDLE) && !txe_s;

    always_ff @(posedge clk) begin
        if (rst) begin
            st <= IDLE;
            rd_n <= 1'b1;
            wr_n <= 1'b1;
            d_oe <= 1'b0;
            rx_valid <= 1'b0;
            n <= '0;
        end else begin
            if (rx_valid && rx_ready) rx_valid <= 1'b0;
            case (st)
                IDLE:
                    if (tx_valid && !txe_s) begin
                        d_out <= tx_data;
                        d_oe <= 1'b1;
                        n <= 4'(WR_SETUP - 1);
                        st <= WR_S;
                    end else if (!rxf_s && !rx_valid) begin
                        rd_n <= 1'b0;
                        n <= 4'(RD_LOW - 1);
                        st <= RD;
                    end
                RD:
                    if (n == 0) begin
                        rx_data <= d_in;
                        rx_valid <= 1'b1;
                        rd_n <= 1'b1;
                        n <= 4'(RECOVER - 1);
                        st <= REC;
                    end else n <= n - 1'b1;
                WR_S:
                    if (n == 0) begin
                        wr_n <= 1'b0;
                        n <= 4'(WR_LOW - 1);
                        st <= WR_L;
                    end else n <= n - 1'b1;
                WR_L:
                    if (n == 0) begin
                        wr_n <= 1'b1;
                        n <= 4'(WR_HOLD - 1);
                        st <= WR_H;
                    end else n <= n - 1'b1;
                WR_H:
                    if (n == 0) begin
                        d_oe <= 1'b0;
                        n <= 4'(RECOVER - 1);
                        st <= REC;
                    end else n <= n - 1'b1;
                REC:
                    if (n == 0) st <= IDLE;
                    else n <= n - 1'b1;
                default: st <= IDLE;
            endcase
        end
    end
endmodule
