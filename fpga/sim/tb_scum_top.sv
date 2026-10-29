// Testbench for scum_top: a behavioural FT2232H (async 245 FIFO, channel B) on one side, the
// DUT pins, INA237 ALERT, reset button and the LMK1D clock on the other. Drives the host
// protocol exactly as the PC will, and checks every register block.
//
//   fpga/sim/run.ps1        (xvlog + xelab + xsim, prints PASS/FAIL per check)
`timescale 1ns / 1ps
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
         scan_rst, scan_out, tl_clk, tl_val_in, tl_dat_in, rdy_out, tl_val_out, tl_dat_out,
         rdy_in, baseband_tx, baseband_rx, nfc_demod, nfc_dbg;
    wire scl, sda, sdio;
    wire [2:0] led;
    logic dut_drives = 0;            // the DUT's outputs, once it is "powered"
    assign scan_out = dut_drives ? 1'b1 : 1'bz;
    assign tl_dat_out = dut_drives ? 1'b0 : 1'bz;
    assign baseband_tx = dut_drives ? 1'b1 : 1'bz;
    pullup (scl);
    pullup (sda);

    scum_top dut (
        .CLK_100M(clk100), .CLK_BUF_1_P(dut_clk), .CLK_BUF_1_N(~dut_clk),
        .FT_D(ft_d), .FT_RXF_N(rxf_n), .FT_TXE_N(txe_n), .FT_RD_N(rd_n), .FT_WR_N(wr_n),
        .FT_SIWU_N(),
        .VIO_EN(vio_en), .VBAT_EN(vbat_en), .VDD_EN(vdd_en),
        .VDDD_SW(vddd_sw), .VDDA_SW(vdda_sw), .RF_VDD_SW(rf_sw),
        .TELEM_ALERT(alert_n), .TELEM_SCL(scl), .TELEM_SDA(sda), .OSC_IN_SEL(osc_in_sel),
        .CORE_RST(core_rst), .BOOT_SEL(boot_sel), .CORE_GPIO(core_gpio),
        .CLK_DEBUG_MUX(clk_debug_mux), .SCAN_CLK(scan_clk), .SCAN_IN(scan_in), .SCAN_EN(scan_en),
        .SCAN_SEL(scan_sel), .SCAN_RST(scan_rst), .SCAN_OUT(scan_out), .TL_CLK(tl_clk),
        .TL_VAL_IN(tl_val_in), .TL_DAT_IN(tl_dat_in), .RDY_OUT(rdy_out),
        .TL_VAL_OUT(tl_val_out), .TL_DAT_OUT(tl_dat_out), .RDY_IN(rdy_in),
        .BASEBAND_TX(baseband_tx), .BASEBAND_RX(baseband_rx), .NFC_DEMOD_MCU(nfc_demod),
        .NFC_MDM_DBG(nfc_dbg), .BTN_CHIP_RST(button_n),
        .FPGA_UART_TX(), .FPGA_UART_RX(1'b1),
        .ADC_DCLK(1'b0), .ADC_nDCLK(1'b0), .ADC_D(10'b0), .ADC_CS(), .ADC_SCLK(), .ADC_RST(),
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

    logic [31:0] v, w;
    byte unsigned r[];
    realtime t0, t1;

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

        $display("---- power and trip");
        wr(A_RAIL_SW, 3'b101);
        check("RAIL_SW pins vddd/vdda/rf", {rf_sw, vdda_sw, vddd_sw}, 3'b101);
        wr(A_RAIL_EN, 3'b111);
        check("RAIL_EN pins vio/vbat/vdd", {vdd_en, vbat_en, vio_en}, 3'b111);
        check("LED3 (DUT powered)", led[2], 1);
        check("CORE_RST driven high once VIO is on", core_rst, 1);
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

        $display("---- DUT I/O");
        dut_drives = 1;
        #100;
        rd(A_DUT_IN, v);
        check("DUT_IN SCAN_OUT (DUT drives 1)", v[PIN_SCAN_OUT], 1);
        check("DUT_IN TL_DAT_OUT (DUT drives 0)", v[PIN_TL_DAT_OUT], 0);
        check("DUT_IN BASEBAND_TX (DUT drives 1)", v[PIN_BASEBAND_TX], 1);
        wr(A_DUT_OUT, DUT_OUT_RESET | (1 << PIN_TL_CLK) | (1 << PIN_SCAN_SEL));
        check("TL_CLK driven 1", tl_clk, 1);
        check("SCAN_SEL driven 1", scan_sel, 1);
        wr(A_DUT_OE, DUT_OE_RESET & ~(1 << PIN_TL_CLK));
        check("TL_CLK released by DUT_OE", tl_clk, 1'bz);
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
