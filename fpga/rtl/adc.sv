// ADC3910D065 (block 0x06): its SPI, its reset pin, a DCLK monitor, and captures of both channels
// into a buffer the host reads back.
//
// The ADC runs in its power-on default: 10-bit DDR on D2-D11 (ADC_D[9:0] here), two's
// complement, channel A centred on DCLK's rising edge and channel B on its falling edge. An IDDR
// per lane (SAME_EDGE_PIPELINED) hands over each A/B pair on one DCLK rising edge, so both
// channels of every sample land in the buffer together. Everything on the capture side runs on
// DCLK itself (through a BUFG); the buffer is a two-clock block RAM, written on DCLK and read on
// the system clock.
//
// A capture: CAP_CTRL.START crosses to the DCLK domain as a toggle, CAP_LEN pairs are written from
// address 0, and a toggle back ends it (CAP_STATUS.DONE). CAP_LEN is only read at the start, while
// it is stable. Without DCLK the capture never finishes, which ADC_DCLK_KHZ = 0 explains.
//
// The SPI is 3-wire: SEN low, R/W, A[14:0] and D[7:0] MSB first on SCLK rising edges (the ADC
// samples on rising, we change SDIO on falling); on a read SDIO is released after the address and
// the ADC drives the data byte, which we sample on rising edges.
module adc
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
    // ADC data interface
    input  logic        dclk,       // DCLK through a BUFG
    input  logic  [9:0] d,          // ADC_D pins (the ADC's D2-D11)
    // ADC control
    output logic        adc_rst,
    output logic        sen_n,
    output logic        sclk,
    output logic        sdio_out,
    output logic        sdio_oe,
    input  logic        sdio_in
);
    localparam int AW = $clog2(CAP_DEPTH);
    wire sel = bus_addr[15:8] == A_ADC_CTRL[15:8];
    wire wr = bus_wr && sel;

    // ================= DCLK domain =================

    logic [9:0] qa, qb;  // A from the rising edge, B from the falling edge after it
    for (genvar i = 0; i < 10; i++) begin : g_iddr
        IDDR #(.DDR_CLK_EDGE("SAME_EDGE_PIPELINED"), .INIT_Q1(1'b0), .INIT_Q2(1'b0), .SRTYPE("SYNC"))
            u_iddr (.Q1(qa[i]), .Q2(qb[i]), .C(dclk), .CE(1'b1), .D(d[i]), .R(1'b0), .S(1'b0));
    end

    logic [19:0] mem [CAP_DEPTH];
    logic start_t = 1'b0, done_t = 1'b0;   // toggles: system -> DCLK, DCLK -> system
    (* ASYNC_REG = "TRUE" *) logic s0 = 1'b0, s1 = 1'b0;
    logic s2 = 1'b0, active = 1'b0;
    logic [AW-1:0] waddr = '0, wlast = '0, len_m1;

    always_ff @(posedge dclk) begin
        s0 <= start_t;
        s1 <= s0;
        s2 <= s1;
        if (s1 != s2) begin
            active <= 1'b1;
            waddr <= '0;
            wlast <= len_m1;  // stable: the host does not change CAP_LEN mid-capture
        end else if (active) begin
            waddr <= waddr + 1'b1;
            if (waddr == wlast) begin
                active <= 1'b0;
                done_t <= !done_t;
            end
        end
    end

    always_ff @(posedge dclk)
        if (active) mem[waddr] <= {qb, qa};

    // DCLK monitor: a Gray-coded cycle count for the system clock to difference every ms.
    logic [15:0] dcnt = '0, dgray = '0;
    always_ff @(posedge dclk) begin
        dcnt <= dcnt + 1'b1;
        dgray <= dcnt ^ (dcnt >> 1);
    end

    // ================= system clock domain =================

    (* ASYNC_REG = "TRUE" *) logic [15:0] g0, g1;
    logic [15:0] gbin, gprev, khz;
    always_comb begin
        gbin[15] = g1[15];
        for (int i = 14; i >= 0; i--) gbin[i] = gbin[i + 1] ^ g1[i];
    end

    (* ASYNC_REG = "TRUE" *) logic d0 = 1'b0, d1 = 1'b0;
    logic d2 = 1'b0;
    logic [AW:0] cap_len;
    logic busy, done;
    assign len_m1 = AW'(cap_len - 1'b1);

    always_ff @(posedge clk) begin
        g0 <= dgray;
        g1 <= g0;
        if (tick_ms) begin
            khz <= gbin - gprev;
            gprev <= gbin;
        end
        d0 <= done_t;
        d1 <= d0;
        d2 <= d1;
        if (rst) begin
            adc_rst <= 1'b0;
            cap_len <= (AW + 1)'(R_CAP_LEN);
            busy <= 1'b0;
            done <= 1'b0;
        end else begin
            if (wr && bus_addr == A_ADC_CTRL) adc_rst <= bus_wdata[ADC_CTRL_RESET];
            if (wr && bus_addr == A_CAP_LEN)
                cap_len <= bus_wdata == 0 ? (AW + 1)'(1)
                         : bus_wdata > CAP_DEPTH ? (AW + 1)'(CAP_DEPTH) : bus_wdata[AW:0];
            if (wr && bus_addr == A_CAP_CTRL && bus_wdata[CAP_CTRL_START] && !busy) begin
                start_t <= !start_t;
                busy <= 1'b1;
                done <= 1'b0;
            end
            if (d1 != d2) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end
    end

    // ---- buffer read port: CAP_DATA returns the word at raddr and steps it on
    logic [AW-1:0] raddr;
    logic [19:0] rword;
    always_ff @(posedge clk) rword <= mem[raddr];  // ready long before the next read strobe

    // ---- SPI master
    typedef enum logic [1:0] {SPI_IDLE, SPI_LOW, SPI_HIGH, SPI_END} spi_t;
    spi_t sst;
    logic [23:0] spi_cmd, spi_sh;
    logic [7:0] spi_rd, half;
    logic [4:0] nbit;
    logic sdio_s;
    sync2 u_sdio (.clk, .d(sdio_in), .q(sdio_s));
    wire spi_read = spi_cmd[ADC_SPI_READ];

    always_ff @(posedge clk) begin
        if (rst) begin
            sst <= SPI_IDLE;
            sen_n <= 1'b1;
            sclk <= 1'b0;
            sdio_oe <= 1'b0;
            sdio_out <= 1'b0;
            spi_cmd <= '0;
        end else begin
            if (half != 0) half <= half - 1'b1;
            case (sst)
                SPI_IDLE:
                    if (wr && bus_addr == A_ADC_SPI) begin
                        spi_cmd <= bus_wdata[23:0];
                        spi_sh <= bus_wdata[23:0];
                        sen_n <= 1'b0;
                        sdio_oe <= 1'b1;
                        sdio_out <= bus_wdata[23];
                        nbit <= '0;
                        half <= 8'(ADC_SPI_HALF - 1);
                        sst <= SPI_LOW;
                    end
                SPI_LOW:  // SCLK low, SDIO set: rise and sample
                    if (half == 0) begin
                        sclk <= 1'b1;
                        if (spi_read && nbit >= 5'd16) spi_rd <= {spi_rd[6:0], sdio_s};
                        half <= 8'(ADC_SPI_HALF - 1);
                        sst <= SPI_HIGH;
                    end
                SPI_HIGH:  // fall, then the next bit (or release SDIO for a read's data byte)
                    if (half == 0) begin
                        sclk <= 1'b0;
                        half <= 8'(ADC_SPI_HALF - 1);
                        if (nbit == 5'd23) begin
                            sdio_oe <= 1'b0;
                            sst <= SPI_END;
                        end else begin
                            nbit <= nbit + 1'b1;
                            spi_sh <= spi_sh << 1;
                            sdio_out <= spi_sh[22];
                            if (spi_read && nbit + 1'b1 >= 5'd16) sdio_oe <= 1'b0;
                            sst <= SPI_LOW;
                        end
                    end
                SPI_END:  // SEN high half a period after the last falling edge
                    if (half == 0) begin
                        sen_n <= 1'b1;
                        sst <= SPI_IDLE;
                    end
                default: sst <= SPI_IDLE;
            endcase
        end
    end

    // ---- register reads (and the CAP_ADDR pointer, which reads step on)
    always_ff @(posedge clk) begin
        rdata <= '0;
        if (rst) raddr <= '0;
        else if (wr && bus_addr == A_CAP_ADDR) raddr <= bus_wdata[AW-1:0];
        if (bus_rd && sel)
            case (bus_addr)
                A_ADC_CTRL:       rdata <= {31'b0, adc_rst};
                A_ADC_SPI:        rdata <= {8'b0, spi_cmd};
                A_ADC_SPI_STATUS: rdata <= {16'b0, spi_rd, 7'b0, sst != SPI_IDLE};
                A_ADC_DCLK_KHZ:   rdata <= {16'b0, khz};
                A_CAP_LEN:        rdata <= 32'(cap_len);
                A_CAP_STATUS:     rdata <= {30'b0, done, busy};
                A_CAP_ADDR:       rdata <= 32'(raddr);
                A_CAP_DATA: begin
                    rdata <= {6'b0, rword[19:10], 6'b0, rword[9:0]};
                    raddr <= raddr + 1'b1;
                end
                default:          rdata <= '0;
            endcase
    end
endmodule
