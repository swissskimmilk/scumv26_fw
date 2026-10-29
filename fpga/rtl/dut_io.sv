// DUT control pins (block 0x03): per-pin drive, readback and timed pulses, plus the front-panel
// DUT reset button.
//
// Every pad is bidirectional and the host chooses the direction (DUT_OE); the reset values
// come from regmap.DUT_PINS. While RAIL_EN.VIO is off every pad is released, so the
// FPGA never back-powers the DUT through its I/O ring.
//
// A pulse inverts one output for DUT_PULSE.US microseconds and then restores it, so it works
// whichever way the pin idles (CORE_RST's polarity is still unconfirmed). The button
// (BTN_CHIP_RST, active low, 10k pull-up) is debounced for 10 ms and fires the same pulse on
// CORE_RST for BUTTON_US.
module dut_io
    import scum_regs_pkg::*;
(
    input  logic             clk,
    input  logic             rst,
    input  logic [15:0]      bus_addr,
    input  logic [31:0]      bus_wdata,
    input  logic             bus_wr,
    input  logic             bus_rd,
    output logic [31:0]      rdata,
    input  logic             tick_ms,
    input  logic             vio_on,
    // pads
    output logic [N_DUT-1:0] pad_out,
    output logic [N_DUT-1:0] pad_oe,
    input  logic [N_DUT-1:0] pad_in,
    input  logic             button_n,
    output logic             button_evt
);
    localparam int US = SYS_HZ / 1_000_000;
    localparam int DEBOUNCE_MS = 10;

    logic [N_DUT-1:0] oe, out, in_s, pulse_mask;
    logic [23:0] pulse_us, button_us;
    logic [$clog2(US)-1:0] us_div;
    logic btn_s, btn_state;
    logic [3:0] btn_ms;
    wire sel = bus_addr[15:8] == A_DUT_OE[15:8];
    wire busy = |pulse_mask;

    sync2 #(.W(N_DUT)) u_in (.clk, .d(pad_in), .q(in_s));
    sync2 #(.INIT(1'b1)) u_btn (.clk, .d(button_n), .q(btn_s));

    always_ff @(posedge clk) begin
        button_evt <= 1'b0;
        if (rst) begin
            oe <= DUT_OE_RESET;
            out <= DUT_OUT_RESET;
            pulse_mask <= '0;
            pulse_us <= '0;
            us_div <= '0;
            button_us <= 24'(R_BUTTON_US);
            btn_state <= 1'b1;
            btn_ms <= '0;
        end else begin
            if (bus_wr && sel)
                case (bus_addr)
                    A_DUT_OE:    oe <= bus_wdata[N_DUT-1:0];
                    A_DUT_OUT:   out <= bus_wdata[N_DUT-1:0];
                    A_BUTTON_US: button_us <= bus_wdata[23:0];
                    default: ;
                endcase

            // pulse timer, in microseconds
            if (busy) begin
                if (us_div == US - 1) begin
                    us_div <= '0;
                    if (pulse_us <= 1) pulse_mask <= '0;
                    else pulse_us <= pulse_us - 1'b1;
                end else us_div <= us_div + 1'b1;
            end else if (bus_wr && sel && bus_addr == A_DUT_PULSE && bus_wdata[23:0] != 0
                         && bus_wdata[28:24] < N_DUT) begin
                pulse_mask <= N_DUT'(1) << bus_wdata[28:24];
                pulse_us <= bus_wdata[23:0];
                us_div <= '0;
            end

            // button: a level must hold for DEBOUNCE_MS before it counts
            if (btn_s == btn_state) btn_ms <= '0;
            else if (tick_ms) begin
                if (btn_ms == DEBOUNCE_MS - 1) begin
                    btn_state <= btn_s;
                    btn_ms <= '0;
                    if (!btn_s) begin  // pressed
                        button_evt <= 1'b1;
                        if (!busy && button_us != 0) begin
                            pulse_mask <= N_DUT'(1) << PIN_CORE_RST;
                            pulse_us <= button_us;
                            us_div <= '0;
                        end
                    end
                end else btn_ms <= btn_ms + 1'b1;
            end
        end
    end

    assign pad_out = out ^ pulse_mask;
    assign pad_oe = vio_on ? oe : '0;

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_DUT_OE:    rdata <= 32'(oe);
                A_DUT_OUT:   rdata <= 32'(out);
                A_DUT_IN:    rdata <= 32'(in_s);
                A_DUT_PULSE: rdata <= {busy, 7'b0, pulse_us};
                A_BUTTON_US: rdata <= {8'b0, button_us};
                default:     rdata <= '0;
            endcase
    end
endmodule
