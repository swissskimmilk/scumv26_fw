// DUT clock counter (block 0x02) and the LMK1D1204's input select.
//
// A free-running 16-bit counter in the DUT clock's own domain (the LMK1D's LVDS output through
// a BUFG) is passed to the system clock as Gray code, so a sample taken mid-increment is off by
// at most one count. 16 bits keeps the carry chain short enough for 500 MHz; the system clock
// adds up the change between its samples into 32 bits (at most ~6 counts per 10 ns sample, so
// the 16-bit difference never wraps). Every CLK_WINDOW system cycles the total is latched:
// f = CLK_COUNT * SYS_HZ / CLK_WINDOW, with 1-count resolution (10 Hz at the default 100 ms).
// No prescaler: the counter runs at the DUT clock itself, which the BUFG passes to about
// 600 MHz on a -2 part (constrained to 500 MHz).
//
// The first window after a restart (reset, or a write to CLK_CTRL or CLK_WINDOW) has no
// previous sample, so it is discarded; CLK_SEQ counts the windows completed since.
module clk_counter
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    // the DUT clock, from a BUFG
    input  logic        dut_clk,
    // LMK1D IN_SEL: released (off), 0 = IN0 (dig), 1 = IN1 (ana)
    output logic        in_sel,
    output logic        in_sel_oe
);
    // ---- DUT clock domain ----
    logic [15:0] cnt = '0, gray = '0;
    always_ff @(posedge dut_clk) begin
        cnt <= cnt + 1'b1;
        gray <= (cnt >> 1) ^ cnt;
    end

    // ---- system clock domain ----
    (* ASYNC_REG = "TRUE" *) logic [15:0] g0, g1;
    logic [15:0] now, prev;
    logic [31:0] acc, count, seq, window, timer;
    logic [1:0]  selr;
    logic        have_last;
    wire sel = bus_addr[15:8] == A_CLK_CTRL[15:8];
    wire restart = bus_wr && sel && (bus_addr == A_CLK_CTRL || bus_addr == A_CLK_WINDOW);
    wire [15:0] step = now - prev;  // edges since the last sample, modulo 2^16

    always_comb begin  // Gray to binary
        now[15] = g1[15];
        for (int i = 14; i >= 0; i--) now[i] = now[i + 1] ^ g1[i];
    end

    always_ff @(posedge clk) begin
        g0 <= gray;
        g1 <= g0;
        prev <= now;
        if (rst) begin
            selr <= '0;
            window <= R_CLK_WINDOW;
            timer <= '0;
            acc <= '0;
            have_last <= 1'b0;
            count <= '0;
            seq <= '0;
        end else if (restart) begin
            if (bus_addr == A_CLK_CTRL) selr <= bus_wdata[1:0];
            else window <= (bus_wdata < 2) ? 32'd2 : bus_wdata;
            timer <= '0;
            acc <= '0;
            have_last <= 1'b0;
            seq <= '0;
        end else if (timer >= window - 1) begin
            timer <= '0;
            acc <= '0;
            have_last <= 1'b1;
            if (have_last) begin  // the first window after a restart spans the switch-over
                count <= acc + step;
                seq <= seq + 1'b1;
            end
        end else begin
            timer <= timer + 1'b1;
            acc <= acc + step;
        end
    end

    assign in_sel = selr == 2'd2;
    assign in_sel_oe = selr == 2'd1 || selr == 2'd2;

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_CLK_CTRL:   rdata <= {30'b0, selr};
                A_CLK_WINDOW: rdata <= window;
                A_CLK_COUNT:  rdata <= count;
                A_CLK_SEQ:    rdata <= seq;
                default:      rdata <= '0;
            endcase
    end
endmodule
