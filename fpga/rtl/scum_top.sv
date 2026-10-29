// SCuMV26 bring-up board, XC7A100T-2FGG676I. Port names are the schematic's net names.
//
// One clock domain for everything the host sees: the 100 MHz oscillator (X1) straight into a
// BUFG, no MMCM. The DUT's clock (through the LMK1D1204) only clocks the counter in
// clk_counter. The host talks to the register bus through the FT2232H's channel B; channel A
// stays the JTAG cable for Vivado.
//
// Not built yet, so their pins sit idle here: the ADC capture and ADC SPI, the I2C master for
// the telemetry bus (INA237s, TLA2528, the VBAT DAC), the DUT UART, and TileLink / scan engines
// (their pins are plain DUT_* GPIO for now).
module scum_top
    import scum_regs_pkg::*;
(
    input  wire        CLK_100M,
    input  wire        CLK_BUF_1_P,
    input  wire        CLK_BUF_1_N,
    // FT2232H channel B, async 245 FIFO
    inout  wire  [7:0] FT_D,
    input  wire        FT_RXF_N,
    input  wire        FT_TXE_N,
    output wire        FT_RD_N,
    output wire        FT_WR_N,
    output wire        FT_SIWU_N,
    // power
    output wire        VIO_EN,
    output wire        VBAT_EN,
    output wire        VDD_EN,
    output wire        VDDD_SW,
    output wire        VDDA_SW,
    output wire        RF_VDD_SW,
    input  wire        TELEM_ALERT,
    inout  wire        TELEM_SCL,
    inout  wire        TELEM_SDA,
    // clock buffer select (released = the LMK1D's outputs off)
    output wire        OSC_IN_SEL,
    // DUT control pins (bit order: scum_regs_pkg PIN_*)
    inout  wire        CORE_RST,
    inout  wire        BOOT_SEL,
    inout  wire        CORE_GPIO,
    inout  wire        CLK_DEBUG_MUX,
    inout  wire        SCAN_CLK,
    inout  wire        SCAN_IN,
    inout  wire        SCAN_EN,
    inout  wire        SCAN_SEL,
    inout  wire        SCAN_RST,
    inout  wire        SCAN_OUT,
    inout  wire        TL_CLK,
    inout  wire        TL_VAL_IN,
    inout  wire        TL_DAT_IN,
    inout  wire        RDY_OUT,
    inout  wire        TL_VAL_OUT,
    inout  wire        TL_DAT_OUT,
    inout  wire        RDY_IN,
    inout  wire        BASEBAND_TX,
    inout  wire        BASEBAND_RX,
    inout  wire        NFC_DEMOD_MCU,
    inout  wire        NFC_MDM_DBG,
    input  wire        BTN_CHIP_RST,
    // DUT UART (not built yet)
    output wire        FPGA_UART_TX,
    input  wire        FPGA_UART_RX,
    // ADC3910 (not built yet)
    input  wire        ADC_DCLK,
    input  wire        ADC_nDCLK,
    input  wire  [9:0] ADC_D,
    output wire        ADC_CS,
    output wire        ADC_SCLK,
    output wire        ADC_RST,
    inout  wire        ADC_SDIO,
    // LEDs: LED1 heartbeat, LED2 host traffic, LED3 DUT powered
    output wire  [2:0] LED
);
    // ---------------- clock and reset ----------------
    wire clk_ibuf, clk;
    IBUF u_clk_ibuf (.I(CLK_100M), .O(clk_ibuf));
    BUFG u_clk_bufg (.I(clk_ibuf), .O(clk));

    // Power-on reset: the shift register configures to all ones and drains in 16 cycles.
    logic [15:0] por = '1;
    always_ff @(posedge clk) por <= {por[14:0], 1'b0};
    wire rst = por[15];

    wire dut_clk_ibuf, dut_clk;
    IBUFDS #(.IOSTANDARD("LVDS_25"), .DIFF_TERM("FALSE"))  // R85 terminates on the board
        u_dut_ibufds (.I(CLK_BUF_1_P), .IB(CLK_BUF_1_N), .O(dut_clk_ibuf));
    BUFG u_dut_bufg (.I(dut_clk_ibuf), .O(dut_clk));

    // ---------------- host link ----------------
    logic [7:0] ft_dout, rx_data, tx_data;
    logic ft_doe, rx_valid, rx_ready, tx_valid, tx_ready;
    ft245_async u_ft (
        .clk, .rst,
        .rxf_n(FT_RXF_N), .txe_n(FT_TXE_N), .rd_n(FT_RD_N), .wr_n(FT_WR_N),
        .d_in(FT_D), .d_out(ft_dout), .d_oe(ft_doe),
        .rx_data, .rx_valid, .rx_ready, .tx_data, .tx_valid, .tx_ready
    );
    assign FT_D = ft_doe ? ft_dout : 8'bz;
    assign FT_SIWU_N = 1'b1;  // send-immediate unused: the FT2232H flushes on its latency timer

    logic [15:0] bus_addr;
    logic [31:0] bus_wdata, rd_sys, rd_pwr, rd_clk, rd_dut;
    logic bus_wr, bus_rd, frame_err, activity;
    host_bridge u_bridge (
        .clk, .rst, .rx_data, .rx_valid, .rx_ready, .tx_data, .tx_valid, .tx_ready,
        .bus_addr, .bus_wdata, .bus_wr, .bus_rd, .bus_rdata(rd_sys | rd_pwr | rd_clk | rd_dut),
        .frame_err, .activity
    );

    // ---------------- blocks ----------------
    logic tick_ms, trip_evt, button_evt, dut_powered;
    sys_ctrl u_sys (
        .clk, .rst, .bus_addr, .bus_wdata, .bus_wr, .bus_rd, .rdata(rd_sys),
        .trip_evt, .button_evt, .frame_evt(frame_err), .tick_ms
    );

    power_ctrl u_pwr (
        .clk, .rst, .bus_addr, .bus_wdata, .bus_wr, .bus_rd, .rdata(rd_pwr),
        .alert_pin(TELEM_ALERT), .vio_en(VIO_EN), .vbat_en(VBAT_EN), .vdd_en(VDD_EN),
        .vddd_sw(VDDD_SW), .vdda_sw(VDDA_SW), .rf_sw(RF_VDD_SW),
        .trip_evt, .dut_powered
    );

    logic in_sel, in_sel_oe;
    clk_counter u_clk (
        .clk, .rst, .bus_addr, .bus_wdata, .bus_wr, .bus_rd, .rdata(rd_clk),
        .dut_clk, .in_sel, .in_sel_oe
    );
    assign OSC_IN_SEL = in_sel_oe ? in_sel : 1'bz;

    logic [N_DUT-1:0] pad_out, pad_oe, pad_in;
    dut_io u_dut (
        .clk, .rst, .bus_addr, .bus_wdata, .bus_wr, .bus_rd, .rdata(rd_dut),
        .tick_ms, .vio_on(VIO_EN), .pad_out, .pad_oe, .pad_in,
        .button_n(BTN_CHIP_RST), .button_evt
    );

    // The pads, in PIN_* order. A tristate per pad; Vivado maps each to an IOBUF.
    `define DUT_PAD(net) \
        assign net = pad_oe[PIN_``net] ? pad_out[PIN_``net] : 1'bz; \
        assign pad_in[PIN_``net] = net;
    `DUT_PAD(CORE_RST)
    `DUT_PAD(BOOT_SEL)
    `DUT_PAD(CORE_GPIO)
    `DUT_PAD(CLK_DEBUG_MUX)
    `DUT_PAD(SCAN_CLK)
    `DUT_PAD(SCAN_IN)
    `DUT_PAD(SCAN_EN)
    `DUT_PAD(SCAN_SEL)
    `DUT_PAD(SCAN_RST)
    `DUT_PAD(SCAN_OUT)
    `DUT_PAD(TL_CLK)
    `DUT_PAD(TL_VAL_IN)
    `DUT_PAD(TL_DAT_IN)
    `DUT_PAD(RDY_OUT)
    `DUT_PAD(TL_VAL_OUT)
    `DUT_PAD(TL_DAT_OUT)
    `DUT_PAD(RDY_IN)
    `DUT_PAD(BASEBAND_TX)
    `DUT_PAD(BASEBAND_RX)
    `DUT_PAD(NFC_DEMOD_MCU)
    `DUT_PAD(NFC_MDM_DBG)
    `undef DUT_PAD

    // ---------------- LEDs ----------------
    logic [9:0] beat_ms, host_ms;
    logic beat;
    always_ff @(posedge clk) begin
        if (rst) begin
            beat_ms <= '0;
            host_ms <= '0;
            beat <= 1'b0;
        end else begin
            if (tick_ms) begin
                beat_ms <= (beat_ms == 499) ? '0 : beat_ms + 1'b1;
                if (beat_ms == 499) beat <= !beat;
                if (host_ms != 0) host_ms <= host_ms - 1'b1;
            end
            if (activity) host_ms <= 10'd50;  // a 50 ms blink per packet
        end
    end
    assign LED = {dut_powered, host_ms != 0, beat};

    // ---------------- not built yet: hold these pins idle ----------------
    assign TELEM_SCL = 1'bz;        // released: the 10k pull-ups hold the bus idle
    assign TELEM_SDA = 1'bz;
    assign FPGA_UART_TX = 1'b1;     // UART idle
    assign ADC_CS = 1'b1;           // SEN deselected
    assign ADC_SCLK = 1'b0;
    assign ADC_RST = 1'b0;
    assign ADC_SDIO = 1'bz;
endmodule
