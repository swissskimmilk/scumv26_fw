// Host packets <-> the register bus. Packets (little-endian, see REGISTERS.md):
//   'W' a0 a1 d0 d1 d2 d3  -> 'K'
//   'R' a0 a1              -> d0 d1 d2 d3
//   'B' a0 a1 n0 n1 n2 n3  -> n words, each read from address a (a data port such as a FIFO)
//
// Register bus: one-cycle bus_wr / bus_rd strobes (registered here); every block registers
// its read data on bus_rd, so bus_rdata is valid the cycle after the block sees bus_rd. A read
// may have side effects (popping a FIFO), which is why each strobe is exactly one cycle.
//
// A packet that stalls for TIMEOUT cycles half way is dropped, and a byte that does not start a
// packet is skipped; both pulse frame_err. So the host can always resynchronise by pausing.
module host_bridge
    import scum_regs_pkg::*;
#(
    parameter int TIMEOUT = SYS_HZ / 1000  // 1 ms
) (
    input  logic        clk,
    input  logic        rst,
    // byte streams (ft245_async)
    input  logic [7:0]  rx_data,
    input  logic        rx_valid,
    output logic        rx_ready,
    output logic [7:0]  tx_data,
    output logic        tx_valid,
    input  logic        tx_ready,
    // register bus
    output logic [15:0] bus_addr,
    output logic [31:0] bus_wdata,
    output logic        bus_wr,
    output logic        bus_rd,
    input  logic [31:0] bus_rdata,
    // status
    output logic        frame_err,  // one-cycle pulse
    output logic        activity    // one-cycle pulse per completed packet
);
    typedef enum logic [2:0] {CMD, ARGS, EXEC, RDWAIT, RDDATA, SEND} state_t;
    state_t st;
    logic [7:0]  cmd;
    logic [47:0] args;   // a0 a1 then d0..d3 or n0..n3, shifted in from the top
    logic [2:0]  nargs;  // argument bytes still to come
    logic [31:0] count;  // burst words still to read
    logic [31:0] word;   // reply word being sent
    logic [2:0]  nbytes; // reply bytes still to send
    logic [$clog2(TIMEOUT+1)-1:0] idle;

    assign rx_ready = (st == CMD) || (st == ARGS);
    assign tx_valid = (st == SEND);
    assign tx_data = word[7:0];

    always_ff @(posedge clk) begin
        bus_wr <= 1'b0;
        bus_rd <= 1'b0;
        frame_err <= 1'b0;
        activity <= 1'b0;
        if (rst) begin
            st <= CMD;
            idle <= '0;
        end else case (st)
            CMD:
                if (rx_valid) begin
                    cmd <= rx_data;
                    idle <= '0;
                    case (rx_data)
                        CMD_WRITE, CMD_BURST: begin nargs <= 3'd6; st <= ARGS; end
                        CMD_READ:             begin nargs <= 3'd2; st <= ARGS; end
                        default:              frame_err <= 1'b1;
                    endcase
                end
            ARGS:
                if (rx_valid) begin
                    args <= {rx_data, args[47:8]};
                    idle <= '0;
                    nargs <= nargs - 1'b1;
                    if (nargs == 1) st <= EXEC;
                end else if (idle == TIMEOUT) begin
                    frame_err <= 1'b1;
                    st <= CMD;
                end else idle <= idle + 1'b1;
            EXEC: begin
                // args now holds the packet's bytes right-aligned: a READ's two address bytes
                // arrive in args[47:32] because only two were shifted in.
                if (cmd == CMD_READ) begin
                    bus_addr <= args[47:32];
                    bus_rd <= 1'b1;
                    count <= 32'd1;
                    st <= RDWAIT;
                end else if (cmd == CMD_WRITE) begin
                    bus_addr <= args[15:0];
                    bus_wdata <= args[47:16];
                    bus_wr <= 1'b1;
                    word <= {24'b0, ACK};
                    nbytes <= 3'd1;
                    count <= '0;
                    st <= SEND;
                end else begin  // BURST
                    bus_addr <= args[15:0];
                    count <= args[47:16];
                    if (args[47:16] == 0) begin
                        activity <= 1'b1;
                        st <= CMD;
                    end else begin
                        bus_rd <= 1'b1;
                        st <= RDWAIT;
                    end
                end
            end
            RDWAIT: st <= RDDATA;  // the block sees bus_rd this cycle and registers its data
            RDDATA: begin
                word <= bus_rdata;
                nbytes <= 3'd4;
                count <= count - 1'b1;
                st <= SEND;
            end
            SEND:
                if (tx_ready) begin
                    word <= {8'b0, word[31:8]};
                    nbytes <= nbytes - 1'b1;
                    if (nbytes == 1) begin
                        if (count != 0) begin
                            bus_rd <= 1'b1;
                            st <= RDWAIT;
                        end else begin
                            activity <= 1'b1;
                            st <= CMD;
                        end
                    end
                end
            default: st <= CMD;
        endcase
    end
endmodule
