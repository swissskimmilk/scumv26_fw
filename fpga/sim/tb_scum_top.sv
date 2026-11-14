// Testbench for scum_top: a behavioural FT2232H (async 245 FIFO, channel B) on one side, the
// DUT pins, INA237 ALERT, reset button and the LMK1D clock on the other. Drives the host
// protocol exactly as the PC will, and checks every register block.
//
//   fpga/sim/run.ps1        (xvlog + xelab + xsim, prints PASS/FAIL per check)
`timescale 1ns / 1ps
// A behavioural I2C device: a register pointer and a byte memory, the way the INA237 and the
// DAC53401 look on the wire. The first byte written sets the pointer, later ones are stored at
// it (incrementing); reads return bytes from it. STRETCH_NS holds SCL low after each address ACK.
module i2c_dev_model #(
    parameter logic [6:0] ADDR = 7'h40,
    parameter int STRETCH_NS = 0
) (
    inout wire scl,
    inout wire sda
);
    typedef enum {IDLE, ADR, WRITE, READ, IGNORE} st_t;
    st_t st = IDLE;
    logic sda_low = 0, scl_low = 0;
    assign sda = sda_low ? 1'b0 : 1'bz;
    assign scl = scl_low ? 1'b0 : 1'bz;
    logic [7:0] mem [256];
    logic [7:0] ptr = 0, sh;
    logic rw, first, mack;
    int n = 0, starts = 0, stops = 0;

    always @(negedge sda) if (scl === 1'b1) begin  // START or repeated START
        starts++;
        st = ADR;
        n = 0;
    end
    always @(posedge sda) if (scl === 1'b1 && !sda_low) begin  // STOP
        stops++;
        st = IDLE;
    end
    always @(posedge scl) begin
        if ((st == ADR || st == WRITE) && n < 8) sh = {sh[6:0], sda !== 1'b0};
        if (st == READ && n == 8) mack = sda === 1'b0;
        n++;
    end
    always @(negedge scl) begin
        if (st == IDLE || st == IGNORE) sda_low = 0;
        else if (n == 8) begin            // the ACK slot comes next
            case (st)
                ADR: if (sh[7:1] == ADDR) begin
                         sda_low = 1;
                         rw = sh[0];
                     end else st = IGNORE;
                WRITE: begin
                    sda_low = 1;
                    if (first) ptr = sh;
                    else begin
                        mem[ptr] = sh;
                        ptr++;
                    end
                    first = 0;
                end
                READ: sda_low = 0;        // the master ACKs or NACKs
                default: ;
            endcase
        end else if (n == 9) begin        // the ACK clock is over: next byte
            n = 0;
            case (st)
                ADR: begin
                    sda_low = 0;
                    if (rw) begin
                        st = READ;
                        sh = mem[ptr];
                        ptr++;
                        sda_low = !sh[7];
                    end else begin
                        st = WRITE;
                        first = 1;
                    end
                    if (STRETCH_NS > 0) begin
                        scl_low = 1;
                        #(STRETCH_NS) scl_low = 0;
                    end
                end
                WRITE: sda_low = 0;
                READ: if (mack) begin
                          sh = mem[ptr];
                          ptr++;
                          sda_low = !sh[7];
                      end else begin
                          st = IGNORE;
                          sda_low = 0;
                      end
                default: ;
            endcase
        end else if (st == READ) sda_low = !sh[7 - n];
    end
endmodule

// The DUT's end of serial TileLink, as SCuM-V24B's generated serdes behaves: 164-bit frames LSB
// first, {chanId, opcode, param, size, source, address, data, corrupt, union, last}. It drives
// TL_CLK, runs both shift registers on its rising edges (outputs change TCO_NS after), drops its
// ready between frames, and answers A-channel Get / PutFullData / PutPartialData from a word
// memory with D-channel AccessAckData / AccessAck. STALL makes its ready drop every few bits.
module stl_chip_model #(
    parameter real HALF_NS = 50.0,  // 10 MHz
    parameter real TCO_NS = 3.0
) (
    input  logic run,          // the DUT is powered
    input  logic stall,
    output wire  clk,
    input  wire  val_in, dat_in,   // from the FPGA
    output logic rdy_in,
    output logic val_out, dat_out, // to the FPGA
    input  wire  rdy_out
);
    logic [63:0] mem [logic [63:0]];
    logic [163:0] rx, tx;
    int rn = 0, tn = 0, gap = 0, frames_in = 0, stall_n = 0;
    logic [163:0] outq [$];
    logic receiving = 0, sending = 0;
    logic ck = 0;
    always #(HALF_NS) if (run) ck = ~ck;
    assign clk = run ? ck : 1'bz;
    // outputs: high-Z until powered
    logic rdy_q = 0, val_q = 0, dat_q = 0;
    assign rdy_in = run ? rdy_q : 1'bz;
    assign val_out = run ? val_q : 1'bz;
    assign dat_out = run ? dat_q : 1'bz;

    function automatic logic [163:0] frame(logic [2:0] chan, opcode, param, logic [7:0] size,
                                           logic [7:0] source, logic [63:0] addr, data,
                                           logic [8:0] un);
        return {chan, opcode, param, size, source, addr, data, 1'b0, un, 1'b1};
    endfunction

    always @(posedge ck) begin
        logic take, give;
        take = rdy_q && val_in === 1'b1;
        give = val_q && rdy_out === 1'b1;
        // ---- deserialiser ----
        if (take) begin
            rx[rn] = dat_in;
            rn++;
            if (rn == 164) begin
                logic [2:0] chan, op;
                logic [63:0] a, d;
                logic [7:0] m;
                rn = 0;
                frames_in++;
                chan = rx[163:161]; op = rx[160:158]; a = rx[138:75]; d = rx[74:11]; m = rx[8:1];
                if (chan == 0 && op == 4)
                    outq.push_back(frame(3, 1, 0, rx[154:147], rx[146:139], 0,
                                         mem.exists(a & ~64'h7) ? mem[a & ~64'h7] : 64'h0, 0));
                else if (chan == 0 && (op == 0 || op == 1)) begin
                    logic [63:0] w;
                    w = mem.exists(a & ~64'h7) ? mem[a & ~64'h7] : 64'h0;
                    for (int i = 0; i < 8; i++) if (m[i]) w[8*i +: 8] = d[8*i +: 8];
                    mem[a & ~64'h7] = w;
                    outq.push_back(frame(3, 0, 0, rx[154:147], rx[146:139], 0, 0, 0));
                end
                gap = 2;  // ready drops between frames, as the generated deserialiser's does
            end
        end
        // ---- serialiser ----
        if (give) begin
            tn++;
            tx = tx >> 1;
            if (tn == 164) sending = 0;
        end else if (!sending && outq.size() > 0) begin
            tx = outq.pop_front();
            tn = 0;
            sending = 1;
        end
        #(TCO_NS);
        if (gap > 0) begin
            gap--;
            rdy_q = 0;
        end else if (stall && ++stall_n % 5 == 0) rdy_q = 0;
        else rdy_q = 1;
        val_q = sending;
        dat_q = sending ? tx[0] : 1'b0;
    end
endmodule

module tb_scum_top;
    import scum_regs_pkg::*;

    // ---------------- clocks ----------------
    logic clk100 = 0, dut_clk = 0;
    always #5 clk100 = ~clk100;          // 100 MHz
    real dut_half = 10.0;                // 50 MHz to start
    always #(dut_half) dut_clk = ~dut_clk;

    // ---------------- FT2232H model ----------------
    // PC -> FPGA bytes wait in to_fpga; FPGA -> PC bytes land in from_fpga.
    byte unsigned to_fpga[$], from_fpga[$];
    logic rxf_n = 1, txe_n = 0, rd_n, wr_n;
    wire [7:0] ft_d;
    logic [7:0] ft_drive;
    logic ft_drive_en = 0;
    assign ft_d = ft_drive_en ? ft_drive : 8'bz;
    realtime rd_fall, wr_fall, d_change;
    int timing_errors = 0;
    bit armed = 0;          // ignore the X-to-1 edges on RD#/WR# before the FPGA leaves reset
    initial #500 armed = 1;

    // RXF# low while a byte waits; high for a 50 ns precharge after each RD#.
    always begin
        rxf_n = 1;
        wait (to_fpga.size() > 0);
        rxf_n = 0;
        @(posedge rd_n);
        #50;
    end
    always @(negedge rd_n) if (armed) begin
        rd_fall = $realtime;
        if (to_fpga.size() == 0) begin
            $display("ERROR %0t: RD# with RXF# high", $time);
            timing_errors++;
        end
        #10 begin ft_drive = to_fpga[0]; ft_drive_en = 1; end  // RD# to data <= 14 ns
    end
    always @(posedge rd_n) if (armed) begin
        if ($realtime - rd_fall < 30.0) begin
            $display("ERROR %0t: RD# low only %0.1f ns", $time, $realtime - rd_fall);
            timing_errors++;
        end
        ft_drive_en = 0;
        void'(to_fpga.pop_front());
    end
    always @(ft_d) if (!ft_drive_en) d_change = $realtime;
    always @(negedge wr_n) if (armed) begin
        wr_fall = $realtime;
        if ($realtime - d_change < 5.0) begin
            $display("ERROR %0t: data changed %0.1f ns before WR#", $time, $realtime - d_change);
            timing_errors++;
        end
    end
    always @(posedge wr_n) if (armed) begin : wr_rise
        logic [7:0] got;
        got = ft_d;
        if ($realtime - wr_fall < 30.0) begin
            $display("ERROR %0t: WR# low only %0.1f ns", $time, $realtime - wr_fall);
            timing_errors++;
        end
        from_fpga.push_back(got);
        #4 if (ft_d !== got) begin
            $display("ERROR %0t: data not held after WR#", $time);
            timing_errors++;
        end
    end

    // ---------------- board around the FPGA ----------------
    logic alert_n = 1, button_n = 1;
    wire vio_en, vbat_en, vdd_en, vddd_sw, vdda_sw, rf_sw, osc_in_sel;
    wire core_rst, boot_sel, core_gpio, clk_debug_mux, scan_clk, scan_in, scan_en, scan_sel,
         scan_rst, scan_out, tl_val_in, tl_dat_in, rdy_out, baseband_tx, baseband_rx, nfc_demod,
         nfc_dbg;
    wire tl_clk, tl_val_out, tl_dat_out, rdy_in;
    wire scl, sda, sdio;
    wire [2:0] led;
    logic dut_drives = 0;            // the DUT's outputs, once it is "powered"
    assign scan_out = dut_drives ? 1'b1 : 1'bz;
    logic tl_stall = 0;
    stl_chip_model chip (
        .run(dut_drives), .stall(tl_stall), .clk(tl_clk),
        .val_in(tl_val_in), .dat_in(tl_dat_in), .rdy_in(rdy_in),
        .val_out(tl_val_out), .dat_out(tl_dat_out), .rdy_out(rdy_out)
    );
    // DUT UART: the testbench's own serial port at UART_DIV = 100 (1 Mbaud)
    wire uart_tx;
    logic uart_rx = 1;
    localparam real UART_BIT_NS = 1000.0;
    assign baseband_tx = dut_drives ? 1'b1 : 1'bz;
    pullup (scl);
    pullup (sda);
    // Telemetry bus: a device at 0x40, one at 0x41 that stretches SCL 20 us after its address,
    // and the testbench's own hands on the lines for the stuck-bus checks.
    logic hold_scl = 0, hold_sda = 0;
    assign scl = hold_scl ? 1'b0 : 1'bz;
    assign sda = hold_sda ? 1'b0 : 1'bz;
    i2c_dev_model #(.ADDR(7'h40)) dev40 (.scl, .sda);
    i2c_dev_model #(.ADDR(7'h41), .STRETCH_NS(20_000)) dev41 (.scl, .sda);

    // Power-good: VIO's PG comes up 20 us after its enable (the TPS7A90's soft start) and drops
    // with it; VBAT's and the 1 V rails' follow their enables; the system rails are always good.
    logic vio_pg = 0;
    always @(posedge vio_en) begin
        #20_000;
        if (vio_en === 1'b1) vio_pg = 1;
    end
    always @(negedge vio_en) vio_pg = 0;
    // J16 spares: the "header" drives GPIO[5:3]; GPIO[2:0] are left for the FPGA to drive.
    wire [5:0] gpio;
    assign gpio[5:3] = 3'b101;

    // ADC3910: 32 MHz DCLK with DDR data centred on each edge, as in the datasheet's Figure 5-1.
    // Channel A counts up one per sample; channel B is A inverted, so every pair checks itself.
    // Its SPI is a register file: R/W + A[14:0] + D[7:0] in on SCLK rising, read data out on falling.
    logic adc_dclk = 0;
    logic [9:0] adc_d = '0;
    logic [9:0] adc_n = '0;
    always begin
        adc_dclk = 0;
        #7.8125 adc_d = adc_n;
        #7.8125 adc_dclk = 1;            // A centred on the rising edge
        #7.8125 adc_d = ~adc_n;
        #7.8125 adc_n = adc_n + 1'b1;    // B centred on the falling edge
    end
    wire adc_cs, adc_sclk, adc_rst;
    logic [7:0] adc_reg [int];
    logic adc_sdio_drv = 0, adc_sdio_val = 0, spi_rd = 0;
    logic [23:0] spi_sh = '0;
    logic [7:0] spi_out;
    int spi_n = 0;
    assign sdio = adc_sdio_drv ? adc_sdio_val : 1'bz;
    always @(negedge adc_cs) spi_n = 0;
    always @(posedge adc_cs) begin
        adc_sdio_drv = 0;
        if (spi_n == 24 && !spi_sh[23]) adc_reg[spi_sh[22:8]] = spi_sh[7:0];
    end
    always @(posedge adc_sclk) if (adc_cs === 1'b0) begin
        spi_sh = {spi_sh[22:0], sdio === 1'b1};
        spi_n++;
        if (spi_n == 16) begin
            spi_rd = spi_sh[15];
            spi_out = adc_reg.exists(int'(spi_sh[14:0])) ? adc_reg[int'(spi_sh[14:0])] : 8'h00;
        end
    end
    always @(negedge adc_sclk) if (adc_cs === 1'b0 && spi_rd && spi_n >= 16 && spi_n < 24) begin
        adc_sdio_drv = 1;
        adc_sdio_val = spi_out[23 - spi_n];
    end

    scum_top dut (
        .CLK_100M(clk100), .CLK_BUF_1_P(dut_clk), .CLK_BUF_1_N(~dut_clk),
        .FT_D(ft_d), .FT_RXF_N(rxf_n), .FT_TXE_N(txe_n), .FT_RD_N(rd_n), .FT_WR_N(wr_n),
        .FT_SIWU_N(),
        .VIO_EN(vio_en), .VBAT_EN(vbat_en), .VDD_EN(vdd_en),
        .VDDD_SW(vddd_sw), .VDDA_SW(vdda_sw), .RF_VDD_SW(rf_sw),
        .TELEM_ALERT(alert_n),
        .REG_VIO_PG(vio_pg), .REG_VBAT_PG(vbat_en), .REG_VDDD_PG(vdd_en), .REG_VDDA_PG(vdd_en),
        .REG_RF_VDD_PG(vdd_en), .REG_SYS_1V8_PG(1'b1), .REG_SYS_3V3_PG(1'b1),
        .REG_FPGA_1V8_PG(1'b1), .FPGA_GPIO(gpio),
        .TELEM_SCL(scl), .TELEM_SDA(sda), .OSC_IN_SEL(osc_in_sel),
        .CORE_RST(core_rst), .BOOT_SEL(boot_sel), .CORE_GPIO(core_gpio),
        .CLK_DEBUG_MUX(clk_debug_mux), .SCAN_CLK(scan_clk), .SCAN_IN(scan_in), .SCAN_EN(scan_en),
        .SCAN_SEL(scan_sel), .SCAN_RST(scan_rst), .SCAN_OUT(scan_out), .TL_CLK(tl_clk),
        .TL_VAL_IN(tl_val_in), .TL_DAT_IN(tl_dat_in), .RDY_OUT(rdy_out),
        .TL_VAL_OUT(tl_val_out), .TL_DAT_OUT(tl_dat_out), .RDY_IN(rdy_in),
        .BASEBAND_TX(baseband_tx), .BASEBAND_RX(baseband_rx), .NFC_DEMOD_MCU(nfc_demod),
        .NFC_MDM_DBG(nfc_dbg), .BTN_CHIP_RST(button_n),
        .FPGA_UART_TX(uart_tx), .FPGA_UART_RX(uart_rx),
        .ADC_DCLK(adc_dclk), .ADC_nDCLK(~adc_dclk), .ADC_D(adc_d), .ADC_CS(adc_cs), .ADC_SCLK(adc_sclk),
        .ADC_RST(adc_rst),
        .ADC_SDIO(sdio), .LED(led)
    );

    // ---------------- host side ----------------
    int fails = 0, checks = 0;

    task automatic check(string what, logic [31:0] got, logic [31:0] want);
        checks++;
        if (got !== want) begin
            fails++;
            $display("FAIL %-44s got 0x%08h want 0x%08h", what, got, want);
        end else $display("pass %-44s 0x%08h", what, got);
    endtask

    task automatic check_range(string what, longint got, longint lo, longint hi);
        checks++;
        if (got < lo || got > hi) begin
            fails++;
            $display("FAIL %-44s got %0d want %0d..%0d", what, got, lo, hi);
        end else $display("pass %-44s %0d", what, got);
    endtask

    task automatic send(byte unsigned b[]);
        foreach (b[i]) to_fpga.push_back(b[i]);
    endtask

    task automatic recv(int n, output byte unsigned b[]);
        b = new[n];
        for (int i = 0; i < n; i++) begin
            wait (from_fpga.size() > 0);
            b[i] = from_fpga.pop_front();
        end
    endtask

    task automatic wr(logic [15:0] a, logic [31:0] d);
        byte unsigned r[];
        send('{CMD_WRITE, a[7:0], a[15:8], d[7:0], d[15:8], d[23:16], d[31:24]});
        recv(1, r);
        if (r[0] != ACK) begin
            fails++;
            $display("FAIL write 0x%04h: reply 0x%02h, not K", a, r[0]);
        end
    endtask

    task automatic rd(logic [15:0] a, output logic [31:0] d);
        byte unsigned r[];
        send('{CMD_READ, a[7:0], a[15:8]});
        recv(4, r);
        d = {r[3], r[2], r[1], r[0]};
    endtask

    // One I2C transaction: returns I2C_STATUS once BUSY has gone.
    task automatic i2c(logic [31:0] cmd, output logic [31:0] status);
        wr(A_I2C_CMD, cmd);
        do rd(A_I2C_STATUS, status); while (status[I2C_STATUS_BUSY]);
    endtask

    function automatic logic [31:0] i2c_cmd(logic [6:0] addr, int wlen, int rlen, bit sep = 0);
        return addr | (wlen << I2C_CMD_WLEN) | (rlen << I2C_CMD_RLEN) | (sep << I2C_CMD_SEP);
    endfunction

    // Serial TileLink frames as the host packs them: six TL_TX words, LSB first.
    function automatic logic [163:0] tl_a(logic [2:0] op, logic [63:0] addr, logic [63:0] data,
                                          logic [7:0] mask, logic [7:0] source = 0);
        return {3'd0, op, 3'd0, 8'd2, source, addr, data, 1'b0, {1'b0, mask}, 1'b1};
    endfunction

    task automatic tl_send(logic [163:0] f);
        for (int i = 0; i < 6; i++) wr(A_TL_TX, 32'(f >> (32 * i)));
    endtask

    task automatic tl_recv(int frames, output logic [163:0] got[]);
        logic [31:0] s;
        byte unsigned b[];
        got = new[frames];
        do rd(A_TL_STATUS, s); while (s[TL_STATUS_RX_WORDS +: 12] < 6 * frames);
        send('{CMD_BURST, A_TL_RX[7:0], A_TL_RX[15:8], 8'(6 * frames), 8'd0, 8'd0, 8'd0});
        recv(24 * frames, b);
        for (int f = 0; f < frames; f++) begin
            got[f] = '0;
            for (int i = 0; i < 24; i++) got[f] |= 164'(b[24 * f + i]) << (8 * i);
        end
    endtask

    // one byte from FPGA_UART_TX
    task automatic uart_get(output logic [7:0] b);
        @(negedge uart_tx);
        #(UART_BIT_NS * 1.5);
        for (int i = 0; i < 8; i++) begin
            b[i] = uart_tx;
            #(UART_BIT_NS);
        end
    endtask

    task automatic uart_put(logic [7:0] b, bit stop = 1);
        uart_rx = 0;
        #(UART_BIT_NS);
        for (int i = 0; i < 8; i++) begin
            uart_rx = b[i];
            #(UART_BIT_NS);
        end
        uart_rx = stop;
        #(UART_BIT_NS);
        uart_rx = 1;
        #(UART_BIT_NS);
    endtask

    logic [31:0] v, w;
    byte unsigned r[];
    realtime t0, t1;
    logic [163:0] f[];
    logic [7:0] ub0, ub1;

    initial begin
        #1000;

        $display("---- link and system registers");
        rd(A_ID, v);        check("ID", v, ID_MAGIC);
        rd(A_VERSION, v);   check("VERSION", v, VERSION);
        rd(A_FEATURES, v);  check("FEATURES", v, R_FEATURES);
        wr(A_SCRATCH, 32'hDEADBEEF);
        rd(A_SCRATCH, v);   check("SCRATCH after write", v, 32'hDEADBEEF);
        rd(16'h00F0, v);    check("unmapped address reads 0", v, 0);
        // burst: three reads of SCRATCH in one packet
        send('{CMD_BURST, A_SCRATCH[7:0], A_SCRATCH[15:8], 8'd3, 8'd0, 8'd0, 8'd0});
        recv(12, r);
        check("burst word 0", {r[3], r[2], r[1], r[0]}, 32'hDEADBEEF);
        check("burst word 2", {r[11], r[10], r[9], r[8]}, 32'hDEADBEEF);
        rd(A_UPTIME_MS, v); check_range("UPTIME_MS (just after start)", v, 0, 2);

        $display("---- framing");
        send('{CMD_WRITE, 8'h08, 8'h00});   // stalls half way
        #1_200_000;                          // > 1 ms
        send('{8'h00});                      // a byte that starts no packet
        rd(A_FRAME_COUNT, v);  check("FRAME_COUNT after timeout + junk", v, 2);
        rd(A_SCRATCH, v);      check("stalled write did not land", v, 32'hDEADBEEF);
        rd(A_EVENTS, v);       check("EVENTS.FRAME set", v[EVENTS_FRAME], 1);
        wr(A_EVENTS, 32'h7);
        rd(A_EVENTS, v);       check("EVENTS cleared by w1c", v, 0);

        $display("---- DUT pads with VIO off");
        check("CORE_RST released while VIO off", core_rst, 1'bz);
        check("TL_CLK released while VIO off", tl_clk, 1'bz);
        check("UART TX released while VIO off", uart_tx, 1'bz);

        $display("---- power and trip");
        wr(A_RAIL_SW, 3'b101);
        check("RAIL_SW pins vddd/vdda/rf", {rf_sw, vdda_sw, vddd_sw}, 3'b101);
        wr(A_RAIL_EN, 3'b111);
        check("RAIL_EN pins vio/vbat/vdd", {vdd_en, vbat_en, vio_en}, 3'b111);
        check("LED3 (DUT powered)", led[2], 1);
        check("CORE_RST released until VIO's PG", core_rst, 1'bz);
        rd(A_RAIL_PG, v);      check("RAIL_PG before VIO's PG", v, 32'hFE);
        #25_000;
        rd(A_RAIL_PG, v);      check("RAIL_PG all good", v, 32'hFF);
        check("CORE_RST driven high once VIO is good", core_rst, 1);
        alert_n = 0;                         // ALERT with the trip disabled: nothing happens
        #200;
        check("ALERT ignored while TRIP_CTRL.EN=0", {vdd_en, vbat_en, vio_en}, 3'b111);
        wr(A_TRIP_CTRL, 32'h1);              // enable: ALERT is still low, so it trips now
        #50;
        check("trip dropped every rail", {vdd_en, vbat_en, vio_en}, 3'b000);
        rd(A_TRIP_STATUS, v);  check("TRIP_STATUS tripped + alert", v, 32'h3);
        alert_n = 1;
        wr(A_RAIL_EN, 3'b001);
        check("RAIL_EN ignored while tripped", vio_en, 0);
        wr(A_TRIP_STATUS, 32'h1);
        wr(A_RAIL_EN, 3'b001);
        check("RAIL_EN works after clearing", vio_en, 1);
        rd(A_TRIP_COUNT, v);   check("TRIP_COUNT", v, 1);
        rd(A_EVENTS, v);       check("EVENTS.TRIP", v[EVENTS_TRIP], 1);
        wr(A_TRIP_CTRL, 32'h0);
        #25_000;                             // VIO's PG again
        rd(A_RAIL_PG, v);      check("RAIL_PG with only VIO on", v, 32'hE1);

        $display("---- DUT I/O");
        dut_drives = 1;
        #100;
        rd(A_DUT_IN, v);
        check("DUT_IN SCAN_OUT (DUT drives 1)", v[PIN_SCAN_OUT], 1);
        check("DUT_IN TL_DAT_OUT (DUT drives 0)", v[PIN_TL_DAT_OUT], 0);
        check("DUT_IN BASEBAND_TX (DUT drives 1)", v[PIN_BASEBAND_TX], 1);
        check("TL_CLK not driven by the FPGA at reset", dut.pad_oe[PIN_TL_CLK], 0);
        wr(A_DUT_OUT, DUT_OUT_RESET | (1 << PIN_SCAN_SEL));
        check("SCAN_SEL driven 1", scan_sel, 1);
        wr(A_DUT_OE, DUT_OE_RESET & ~(1 << PIN_SCAN_SEL));
        check("SCAN_SEL released by DUT_OE", scan_sel, 1'bz);
        wr(A_DUT_OE, DUT_OE_RESET);
        // pulse CORE_RST (idles high) low for 5 us
        fork  // time the pulse from its first edge, not from the write's acknowledgement
            begin
                wait (core_rst === 1'b0);
                t0 = $realtime;
                wait (core_rst === 1'b1);
                t1 = $realtime;
            end
        join_none
        wr(A_DUT_PULSE, (PIN_CORE_RST << 24) | 5);
        rd(A_DUT_PULSE, v);    check("DUT_PULSE busy during pulse", v[31], 1);
        wait (core_rst === 1'b1);
        #1;
        check_range("CORE_RST pulse width (ns)", longint'(t1 - t0), 4990, 5010);
        rd(A_DUT_PULSE, v);    check("DUT_PULSE idle after", v[31], 0);

        $display("---- button");
        button_n = 0; #3_000_000; button_n = 1;   // a 3 ms bounce: ignored
        #12_000_000;
        rd(A_BUTTON_COUNT, v); check("3 ms press ignored (debounce)", v, 0);
        button_n = 0;
        wait (core_rst === 1'b0);                 // after 10 ms of steady press
        t0 = $realtime;
        wait (core_rst === 1'b1);
        t1 = $realtime;
        check_range("button CORE_RST pulse (ns, BUTTON_US=1000)", longint'(t1 - t0), 999_000, 1_001_000);
        button_n = 1;
        #11_000_000;
        rd(A_BUTTON_COUNT, v); check("BUTTON_COUNT", v, 1);

        $display("---- header GPIO");
        check("GPIO released at reset", gpio[2:0], 3'bzzz);
        wr(A_GPIO_OUT, 6'b000_101);
        wr(A_GPIO_OE, 6'b000_111);
        check("GPIO[2:0] driven", gpio[2:0], 3'b101);
        rd(A_GPIO_IN, v);      check("GPIO_IN", v, 32'b101_101);
        wr(A_GPIO_OE, 0);
        check("GPIO released by GPIO_OE", gpio[2:0], 3'bzzz);

        $display("---- clock counter");
        wr(A_CLK_CTRL, 2);
        check("OSC_IN_SEL = 1 for ana", osc_in_sel, 1);
        wr(A_CLK_CTRL, 1);
        check("OSC_IN_SEL = 0 for dig", osc_in_sel, 0);
        wr(A_CLK_WINDOW, 10_000);             // 100 us
        do rd(A_CLK_SEQ, v); while (v < 2);
        rd(A_CLK_COUNT, v);
        check_range("50 MHz over 100 us", v, 4999, 5001);
        dut_half = 1.0;                       // 500 MHz
        wr(A_CLK_WINDOW, 10_000);
        do rd(A_CLK_SEQ, v); while (v < 2);
        rd(A_CLK_COUNT, v);
        check_range("500 MHz over 100 us", v, 49_999, 50_001);
        dut_half = 15_258.789;                // 32.768 kHz
        wr(A_CLK_WINDOW, 1_000_000);          // 10 ms
        do rd(A_CLK_SEQ, v); while (v < 2);
        rd(A_CLK_COUNT, v);
        check_range("32.768 kHz over 10 ms", v, 327, 328);
        wr(A_CLK_CTRL, 0);
        check("OSC_IN_SEL released for off", osc_in_sel, 1'bz);

        $display("---- telemetry I2C");
        check("FEATURES.I2C", R_FEATURES[FEATURES_I2C], 1);
        rd(A_I2C_STATUS, v);   check("I2C idle, both lines high", v, (1 << I2C_STATUS_SCL) | (1 << I2C_STATUS_SDA));
        // write: pointer 0x05, then 0x12 0x34 (an INA237-style 16-bit register write)
        wr(A_I2C_WDATA, 32'h0034_1205);
        fork
            begin  // time SCL while it runs: 100 kHz by default
                @(posedge scl) t0 = $realtime;
                @(posedge scl) t1 = $realtime;
            end
        join_none
        i2c(i2c_cmd(7'h40, 3, 0), v);
        check("write: no NACK", v[4:0], 0);
        check("write landed (byte 0x05)", dev40.mem[5], 8'h12);
        check("write landed (byte 0x06)", dev40.mem[6], 8'h34);
        check_range("SCL period at I2C_DIV=250 (ns)", longint'(t1 - t0), 9_990, 10_200);
        // read back through a repeated START
        w = dev40.starts;
        wr(A_I2C_WDATA, 32'h05);
        i2c(i2c_cmd(7'h40, 1, 2), v);
        check("read (repeated START): no NACK", v[4:0], 0);
        rd(A_I2C_RDATA, v);    check("read bytes, first in [7:0]", v, 32'h3412);
        check("START + repeated START seen", dev40.starts - w, 2);
        // the same with STOP + START between write and read
        w = dev40.stops;
        i2c(i2c_cmd(7'h40, 1, 2, 1), v);
        rd(A_I2C_RDATA, v);    check("read (SEP)", v, 32'h3412);
        check("SEP: a STOP between, and one at the end", dev40.stops - w, 2);
        // four bytes each way, and a read with no write first (from the pointer)
        wr(A_I2C_WDATA, 32'hDDCC_BB10);
        i2c(i2c_cmd(7'h40, 4, 0), v);
        wr(A_I2C_WDATA, 32'h10);
        i2c(i2c_cmd(7'h40, 1, 0), v);
        i2c(i2c_cmd(7'h40, 0, 3), v);
        rd(A_I2C_RDATA, v);    check("plain 3-byte read", v, 32'hDDCCBB);
        wr(A_I2C_WDATA, 32'h10);
        i2c(i2c_cmd(7'h40, 1, 4), v);
        rd(A_I2C_RDATA, v);    check("4-byte read", v[23:0], 24'hDDCCBB);
        // probes and NACKs
        i2c(i2c_cmd(7'h40, 0, 0), v);
        check("probe 0x40: acknowledged", v[4:0], 0);
        i2c(i2c_cmd(7'h22, 1, 2), v);
        check("0x22: ANACK, nothing else", v[4:0], 1 << I2C_STATUS_ANACK);
        // a device that stretches SCL after its address
        wr(A_I2C_WDATA, 32'h0000_5A07);
        i2c(i2c_cmd(7'h41, 2, 0), v);
        wr(A_I2C_WDATA, 32'h07);
        i2c(i2c_cmd(7'h41, 1, 1), v);
        rd(A_I2C_RDATA, v);
        check("read through clock stretching", v, 32'h5A);
        // 400 kHz
        wr(A_I2C_DIV, 63);
        wr(A_I2C_WDATA, 32'h05);
        i2c(i2c_cmd(7'h40, 1, 2), v);
        rd(A_I2C_RDATA, v);    check("read at I2C_DIV=63", v, 32'h3412);
        wr(A_I2C_DIV, R_I2C_DIV);
        // a stuck bus: SDA held low -> STUCK, then RECOVER clocks it free
        hold_sda = 1;
        #1000;
        i2c(i2c_cmd(7'h40, 0, 0), v);
        check("SDA held low: STUCK, nothing sent", v[4:0], 1 << I2C_STATUS_STUCK);
        fork
            begin  // a device that lets go after three clocks
                repeat (3) @(negedge scl);
                hold_sda = 0;
            end
        join_none
        i2c(1 << I2C_CMD_RECOVER, v);
        check("RECOVER frees the bus", v, (1 << I2C_STATUS_SCL) | (1 << I2C_STATUS_SDA));
        i2c(i2c_cmd(7'h40, 0, 0), v);
        check("probe works after RECOVER", v[4:0], 0);
        // a device that holds SCL low for good: TIMEOUT after 10 ms, lines released
        wr(A_I2C_WDATA, 32'h05);
        fork
            begin
                @(negedge scl);
                #3000 hold_scl = 1;
            end
        join_none
        i2c(i2c_cmd(7'h40, 1, 2), v);
        check("SCL held low: TIMEOUT", v[4:0], 1 << I2C_STATUS_TIMEOUT);
        check("FPGA released SDA after TIMEOUT", dut.sda_oe, 0);
        hold_scl = 0;
        #100_000;

        $display("---- ADC3910");
        check("FEATURES.ADC", R_FEATURES[FEATURES_ADC], 1);
        // the model's quarter period rounds to 7.813 ns at 1 ps resolution: 31.252 ns, 31998/ms
        rd(A_ADC_DCLK_KHZ, v); check_range("ADC_DCLK_KHZ (32 MHz)", v, 31_990, 32_010);
        check("ADC_RST low at start", adc_rst, 0);
        wr(A_ADC_CTRL, 1);
        check("ADC_RST follows ADC_CTRL.RESET", adc_rst, 1);
        wr(A_ADC_CTRL, 0);
        check("SEN idle high", adc_cs, 1);
        wr(A_ADC_SPI, (16'h039 << ADC_SPI_ADDR) | 8'hA5);       // write SPARE_REG
        do rd(A_ADC_SPI_STATUS, v); while (v[ADC_SPI_STATUS_BUSY]);
        check("SPI write landed (0x039 = 0xA5)", adc_reg[32'h039], 8'hA5);
        adc_reg[32'h038] = 8'h01;                                 // CFG_ALERT
        wr(A_ADC_SPI, (1 << ADC_SPI_READ) | (16'h039 << ADC_SPI_ADDR));
        do rd(A_ADC_SPI_STATUS, v); while (v[ADC_SPI_STATUS_BUSY]);
        check("SPI read 0x039", v[ADC_SPI_STATUS_RDATA +: 8], 8'hA5);
        wr(A_ADC_SPI, (1 << ADC_SPI_READ) | (16'h038 << ADC_SPI_ADDR));
        do rd(A_ADC_SPI_STATUS, v); while (v[ADC_SPI_STATUS_BUSY]);
        check("SPI read 0x038", v[ADC_SPI_STATUS_RDATA +: 8], 8'h01);
        check("FPGA released SDIO after the frame", dut.adc_sdio_oe, 0);
        // a 1000-pair capture, read back with one burst
        wr(A_CAP_LEN, 1000);
        rd(A_CAP_STATUS, v);   check("CAP_STATUS before any capture", v, 0);
        wr(A_CAP_CTRL, 1);
        rd(A_CAP_STATUS, v);   check("CAP_STATUS busy", v, 1 << CAP_STATUS_BUSY);
        do rd(A_CAP_STATUS, v); while (v[CAP_STATUS_BUSY]);
        check("CAP_STATUS done", v, 1 << CAP_STATUS_DONE);
        wr(A_CAP_ADDR, 0);
        send('{CMD_BURST, A_CAP_DATA[7:0], A_CAP_DATA[15:8], 8'(1000), 8'(1000 >> 8), 8'd0, 8'd0});
        recv(4000, r);
        begin
            automatic int bad_pair = 0, bad_step = 0;
            automatic logic [9:0] a, b, prev;
            for (int i = 0; i < 1000; i++) begin
                a = {r[4*i+1][1:0], r[4*i]};
                b = {r[4*i+3][1:0], r[4*i+2]};
                if (b !== ~a) bad_pair++;
                if (i > 0 && a !== prev + 10'd1) bad_step++;
                prev = a;
            end
            check("every pair: B is A inverted (same sample)", bad_pair, 0);
            check("A counts up one per pair (no gaps)", bad_step, 0);
        end
        rd(A_CAP_ADDR, v);     check("CAP_ADDR stepped by the burst", v, 1000);
        wr(A_CAP_LEN, 0);
        rd(A_CAP_LEN, v);      check("CAP_LEN 0 reads as 1", v, 1);
        wr(A_CAP_LEN, 70000);
        rd(A_CAP_LEN, v);      check("CAP_LEN clamps to CAP_DEPTH", v, CAP_DEPTH);

        $display("---- serial TileLink");
        check("FEATURES.TL", R_FEATURES[FEATURES_TL], 1);
        #1_100_000;  // a whole millisecond of TL_CLK
        rd(A_TL_CLK_KHZ, v);   check_range("TL_CLK_KHZ (10 MHz from the DUT)", v, 9_990, 10_010);
        rd(A_TL_FRAME, v);     check("TL_FRAME default", v, 164);
        wr(A_TL_TX, 32'h1234);
        rd(A_TL_STATUS, v);    check("TL_TX ignored while disabled", v[TL_STATUS_TX_FREE +: 12], TL_FIFO);
        check("TL_VAL_IN is DUT GPIO while disabled", dut.pad_oe[PIN_TL_VAL_IN] && tl_val_in === 1'b0, 1);
        wr(A_TL_CTRL, 1);
        #1000;
        check("engine ready for the DUT's frames (RDY_OUT)", rdy_out, 1);
        check("engine idle (TL_VAL_IN low)", tl_val_in, 0);
        // PutFullData 0xCAFEF00D to 0x8000_0010, then read it back with a Get
        tl_send(tl_a(0, 64'h8000_0010, 64'hCAFE_F00D, 8'h0F));
        tl_recv(1, f);
        check("Put: AccessAck (chan 3, opcode 0)", {f[0][163:161], f[0][160:158]}, {3'd3, 3'd0});
        check("Put landed in the DUT's memory", chip.mem[64'h8000_0010][31:0], 32'hCAFE_F00D);
        tl_send(tl_a(4, 64'h8000_0010, 0, 8'h0F));
        tl_recv(1, f);
        check("Get: AccessAckData (chan 3, opcode 1)", {f[0][163:161], f[0][160:158]}, {3'd3, 3'd1});
        check("Get: data", f[0][42:11], 32'hCAFE_F00D);
        check("Get: size echoed", f[0][154:147], 2);
        // three requests queued back to back, with the DUT's ready dropping every fifth bit
        tl_stall = 1;
        tl_send(tl_a(1, 64'h8000_0020, 64'h1111_2222_3333_4444, 8'hF0, 1));
        tl_send(tl_a(4, 64'h8000_0020, 0, 8'hFF, 2));
        tl_send(tl_a(4, 64'h8000_0010, 0, 8'hFF, 3));
        rd(A_TL_STATUS, v);    check("TX_BUSY while frames go out", v[TL_STATUS_TX_BUSY], 1);
        tl_recv(3, f);
        tl_stall = 0;
        check("stalled: all 5 frames reached the DUT", chip.frames_in, 5);
        check("PutPartial: AccessAck, source 1", {f[0][160:158], f[0][146:139]}, {3'd0, 8'd1});
        check("PutPartial: masked bytes written", f[1][74:43], 32'h1111_2222);
        check("PutPartial: unmasked bytes untouched", f[1][42:11], 32'h0);
        check("Get order and source", {f[2][146:139], f[2][42:11]}, {8'd3, 32'hCAFE_F00D});
        rd(A_TL_STATUS, v);    check("TL_STATUS idle and empty", v, TL_FIFO << TL_STATUS_TX_FREE);
        rd(A_TL_RX, v);        check("TL_RX reads 0 when empty", v, 0);
        // disabled: the pads go back to DUT GPIO and the FIFOs empty
        tl_send(tl_a(4, 64'h8000_0010, 0, 8'hFF));
        wr(A_TL_CTRL, 0);
        #1000;
        rd(A_TL_STATUS, v);    check("TL_CTRL off empties the FIFOs", v, TL_FIFO << TL_STATUS_TX_FREE);
        check("RDY_OUT back to DUT_OUT's level", rdy_out, 0);

        $display("---- DUT UART");
        check("FEATURES.UART", R_FEATURES[FEATURES_UART], 1);
        check("UART TX idles high", uart_tx, 1);
        wr(A_UART_DIV, 100);
        fork
            begin
                uart_get(ub0);
                uart_get(ub1);
            end
            begin
                wr(A_UART_TX, "H");
                wr(A_UART_TX, "i");
            end
        join
        check("TX: two bytes, in order", {ub0, ub1}, {8'h48, 8'h69});
        uart_put(8'h55);
        uart_put(8'hA3);
        rd(A_UART_STATUS, v);  check("RX_COUNT 2", v[UART_STATUS_RX_COUNT +: 12], 2);
        send('{CMD_BURST, A_UART_RX[7:0], A_UART_RX[15:8], 8'd3, 8'd0, 8'd0, 8'd0});
        recv(12, r);
        check("RX byte 0 (VALID)", {r[1], r[0]}, 16'h155);
        check("RX byte 1 (VALID)", {r[5], r[4]}, 16'h1A3);
        check("RX empty: VALID 0", {r[9], r[8]}, 16'h000);
        rd(A_UART_EVENTS, v);  check("no UART errors", v, 0);
        uart_put(8'h0F, 0);    // stop bit low
        rd(A_UART_EVENTS, v);  check("UART_EVENTS.FRAMING", v, 1 << UART_EVENTS_FRAMING);
        wr(A_UART_EVENTS, 3);
        rd(A_UART_EVENTS, v);  check("UART_EVENTS cleared", v, 0);
        rd(A_UART_RX, v);      check("framing-error byte kept", v, 32'h10F);

        $display("---- summary");
        check("FT2232H strobe timing errors", timing_errors, 0);
        if (fails == 0) $display("ALL %0d CHECKS PASSED", checks);
        else $display("%0d OF %0d CHECKS FAILED", fails, checks);
        $finish;
    end

    initial begin
        #2_000_000_000;
        $display("TIMEOUT");
        $finish;
    end
endmodule
