// DUT power (block 0x01): regulator enables, rail-source switches, and the over-current trip.
//
// The trip is pure hardware, with no software in the path: TELEM_ALERT (the six INA237s' shared open-drain ALERT) drops all three
// enables two clock edges after it reaches the pin, and TRIP_STATUS.TRIPPED then holds them off
// until the host clears it. Sequencing (VIO -> VBAT -> 1 V, with settling delays and monitor
// checks) stays on the PC: it is slow and needs the INA237 readings.
module power_ctrl
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    // pins
    input  logic        alert_pin,
    output logic        vio_en,
    output logic        vbat_en,
    output logic        vdd_en,
    output logic        vddd_sw,
    output logic        vdda_sw,
    output logic        rf_sw,
    // status
    output logic        trip_evt,  // one-cycle pulse when a trip drops the rails
    output logic        dut_powered
);
    logic [2:0] en, sw;
    logic       trip_en, active_high, tripped, alert_s;
    wire sel = bus_addr[15:8] == A_RAIL_EN[15:8];
    wire alert = alert_s ^ ~active_high;  // 1 = asserted

    sync2 #(.INIT(1'b1)) u_alert (.clk, .d(alert_pin), .q(alert_s));

    always_ff @(posedge clk) begin
        trip_evt <= 1'b0;
        if (rst) begin
            en <= '0;
            sw <= '0;
            trip_en <= 1'b0;
            active_high <= 1'b0;
            tripped <= 1'b0;
        end else begin
            if (bus_wr && sel) begin
                if (bus_addr == A_RAIL_EN && !tripped) en <= bus_wdata[2:0];
                if (bus_addr == A_RAIL_SW) sw <= bus_wdata[2:0];
                if (bus_addr == A_TRIP_CTRL) {active_high, trip_en} <= bus_wdata[1:0];
                if (bus_addr == A_TRIP_STATUS && bus_wdata[TRIP_STATUS_TRIPPED]) tripped <= 1'b0;
            end
            if (trip_en && alert) begin  // after the writes: a trip always wins
                en <= '0;
                tripped <= 1'b1;
                trip_evt <= !tripped;
            end
        end
    end

    assign {vdd_en, vbat_en, vio_en} = en;
    assign {rf_sw, vdda_sw, vddd_sw} = sw;
    assign dut_powered = |en;

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_RAIL_EN:     rdata <= {29'b0, en};
                A_RAIL_SW:     rdata <= {29'b0, sw};
                A_TRIP_CTRL:   rdata <= {30'b0, active_high, trip_en};
                A_TRIP_STATUS: rdata <= {30'b0, alert, tripped};
                default:       rdata <= '0;
            endcase
    end
endmodule
