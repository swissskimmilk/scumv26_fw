// System registers (block 0x00): identity, scratch, uptime, and the event flags and counters.
module sys_ctrl
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    // one-cycle event pulses
    input  logic        trip_evt,
    input  logic        button_evt,
    input  logic        frame_evt,
    output logic        tick_ms  // one-cycle pulse every millisecond
);
    localparam int MS = SYS_HZ / 1000;
    logic [$clog2(MS)-1:0] div;
    logic [31:0] scratch, uptime, trips, buttons, frames;
    logic [2:0]  events;
    wire sel = bus_addr[15:8] == A_ID[15:8];

    always_ff @(posedge clk) begin
        tick_ms <= 1'b0;
        if (rst) begin
            div <= '0;
            uptime <= '0;
            scratch <= '0;
            events <= '0;
            trips <= '0;
            buttons <= '0;
            frames <= '0;
        end else begin
            if (div == MS - 1) begin
                div <= '0;
                uptime <= uptime + 1'b1;
                tick_ms <= 1'b1;
            end else div <= div + 1'b1;

            if (bus_wr && sel && bus_addr == A_SCRATCH) scratch <= bus_wdata;
            // set wins over a clear in the same cycle, so no event is lost
            if (bus_wr && sel && bus_addr == A_EVENTS) events <= events & ~bus_wdata[2:0];
            if (trip_evt)   begin events[EVENTS_TRIP]   <= 1'b1; trips   <= trips + 1'b1;   end
            if (button_evt) begin events[EVENTS_BUTTON] <= 1'b1; buttons <= buttons + 1'b1; end
            if (frame_evt)  begin events[EVENTS_FRAME]  <= 1'b1; frames  <= frames + 1'b1;  end
        end
    end

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_ID:           rdata <= ID_MAGIC;
                A_VERSION:      rdata <= VERSION;
                A_SCRATCH:      rdata <= scratch;
                A_UPTIME_MS:    rdata <= uptime;
                A_EVENTS:       rdata <= {29'b0, events};
                A_TRIP_COUNT:   rdata <= trips;
                A_BUTTON_COUNT: rdata <= buttons;
                A_FRAME_COUNT:  rdata <= frames;
                A_FEATURES:     rdata <= R_FEATURES;
                default:        rdata <= '0;
            endcase
    end
endmodule
