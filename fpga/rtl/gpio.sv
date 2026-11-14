// Header GPIO (block 0x04): FPGA_GPIO[5:0] on J16, plain per-pin direction, drive and readback.
// Every pin is released at reset.
module gpio
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    output logic  [5:0] pin_out,
    output logic  [5:0] pin_oe,
    input  logic  [5:0] pin_in
);
    logic [5:0] in_s;
    wire sel = bus_addr[15:8] == A_GPIO_OE[15:8];

    sync2 #(.W(6)) u_in (.clk, .d(pin_in), .q(in_s));

    always_ff @(posedge clk) begin
        if (rst) begin
            pin_oe <= '0;
            pin_out <= '0;
        end else if (bus_wr && sel) begin
            if (bus_addr == A_GPIO_OE) pin_oe <= bus_wdata[5:0];
            if (bus_addr == A_GPIO_OUT) pin_out <= bus_wdata[5:0];
        end
    end

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_GPIO_OE:  rdata <= {26'b0, pin_oe};
                A_GPIO_OUT: rdata <= {26'b0, pin_out};
                A_GPIO_IN:  rdata <= {26'b0, in_s};
                default:    rdata <= '0;
            endcase
    end
endmodule
