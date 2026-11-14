// Telemetry I2C master (block 0x05): the INA237s, the TLA2528 and the DAC53401 on TELEM_SCL/SDA
// (open drain, 4.7k pull-ups to 1.8 V). One transaction per I2C_CMD write; the host builds each
// device's protocol out of transactions (host/scum.py).
//
// A transaction: START, address+W, WLEN bytes from I2C_WDATA, then if RLEN a repeated START (or
// STOP + START with SEP), address+R, RLEN bytes into I2C_RDATA (ACKed but the last), STOP. With
// WLEN = 0 and RLEN > 0 it is a plain read; WLEN = RLEN = 0 sends the address alone, a probe. A
// NACK anywhere ends it with STOP. RECOVER clocks SCL until a device holding SDA low lets go (at
// most 9 pulses), then sends STOP.
//
// Every bit takes four quarters of I2C_DIV system clocks: SCL low in the first, SDA set in the
// second, SCL released for the third and fourth, SDA sampled entering the fourth. A quarter
// that needs SCL high waits for it (clock stretching); 10 ms of waiting ends the transaction.
// At the default 100 kHz that gives 5 us low and 5 us high, with SDA set 2.5 us after SCL falls.
module i2c_master
    import scum_regs_pkg::*;
(
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] bus_addr,
    input  logic [31:0] bus_wdata,
    input  logic        bus_wr,
    input  logic        bus_rd,
    output logic [31:0] rdata,
    output logic        scl_oe,  // 1 = pull SCL low
    output logic        sda_oe,  // 1 = pull SDA low
    input  logic        scl_in,
    input  logic        sda_in
);
    typedef enum logic [2:0] {PH_IDLE, PH_RS, PH_BIT, PH_STOP, PH_RCV} ph_t;
    typedef enum logic [1:0] {ST_AW, ST_WD, ST_AR, ST_RD} st_t;  // address+W, write, address+R, read

    wire sel = bus_addr[15:8] == A_I2C_DIV[15:8];

    logic scl_s, sda_s;
    sync2 #(.W(2), .INIT(2'b11)) u_sync (.clk, .d({scl_in, sda_in}), .q({scl_s, sda_s}));

    logic [15:0] div, qcnt;
    logic [19:0] stretch;
    ph_t ph;
    st_t st;
    logic [2:0] q;        // quarter within the phase
    logic [3:0] bitn;     // bit within the byte, 8 = the ACK slot
    logic [7:0] sh;       // byte going out or coming in
    logic [1:0] idx;      // data byte number
    logic [6:0] addr;
    logic [2:0] wlen, rlen;
    logic sep, restart, ack;
    logic [31:0] cmd, wdata, rd_bytes;
    logic busy, anack, dnack, tout, stuck;

    // A quarter that has released SCL waits until SCL is seen high.
    wire waits = (ph == PH_RS || ph == PH_BIT || ph == PH_STOP || ph == PH_RCV) && q == 3'd2;
    wire stalled = waits && !scl_s;
    wire tick = busy && qcnt == 0 && !stalled;
    wire reading = st == ST_RD;
    wire [2:0] n_w = bus_wdata[I2C_CMD_WLEN +: 3] > 3'd4 ? 3'd4 : bus_wdata[I2C_CMD_WLEN +: 3];
    wire [2:0] n_r = bus_wdata[I2C_CMD_RLEN +: 3] > 3'd4 ? 3'd4 : bus_wdata[I2C_CMD_RLEN +: 3];

    always_ff @(posedge clk) begin
        if (rst) begin
            div <= 16'(R_I2C_DIV);
            cmd <= '0;
            wdata <= '0;
            rd_bytes <= '0;
            busy <= 1'b0;
            {anack, dnack, tout, stuck} <= '0;
            ph <= PH_IDLE;
            scl_oe <= 1'b0;
            sda_oe <= 1'b0;
        end else begin
            // ---- host writes
            if (bus_wr && sel) begin
                if (bus_addr == A_I2C_DIV) div <= bus_wdata[15:0] < 16'd4 ? 16'd4 : bus_wdata[15:0];
                if (bus_addr == A_I2C_WDATA) wdata <= bus_wdata;
                if (bus_addr == A_I2C_CMD && !busy) begin
                    cmd <= bus_wdata;
                    addr <= bus_wdata[I2C_CMD_ADDR +: 7];
                    wlen <= n_w;
                    rlen <= n_r;
                    sep <= bus_wdata[I2C_CMD_SEP];
                    restart <= 1'b0;
                    {anack, dnack, tout, stuck} <= '0;
                    rd_bytes <= '0;
                    idx <= '0;
                    bitn <= '0;
                    qcnt <= div - 1'b1;
                    stretch <= '0;
                    if (bus_wdata[I2C_CMD_RECOVER]) begin
                        busy <= 1'b1;
                        ph <= PH_RCV;
                        q <= '0;
                        sda_oe <= 1'b0;
                        scl_oe <= 1'b1;
                    end else if (!scl_s || !sda_s) begin
                        stuck <= 1'b1;  // someone holds the bus: send nothing
                    end else begin
                        busy <= 1'b1;
                        ph <= PH_RS;    // START from idle = the last two quarters of a repeated START
                        q <= 3'd4;
                        sda_oe <= 1'b1;
                        st <= (n_w != 0 || n_r == 0) ? ST_AW : ST_AR;
                        sh <= {bus_wdata[I2C_CMD_ADDR +: 7], !(n_w != 0 || n_r == 0)};
                    end
                end
            end

            // ---- quarter timing and clock-stretch timeout
            if (busy) begin
                if (stalled) begin
                    stretch <= stretch + 1'b1;
                    if (stretch == 20'(I2C_TIMEOUT - 1)) begin
                        tout <= 1'b1;
                        busy <= 1'b0;
                        ph <= PH_IDLE;
                        scl_oe <= 1'b0;
                        sda_oe <= 1'b0;
                    end
                end else begin
                    stretch <= '0;
                    if (qcnt != 0) qcnt <= qcnt - 1'b1;
                end
            end

            // ---- the bus sequencer, one step per quarter
            if (tick) begin
                qcnt <= div - 1'b1;
                case (ph)
                    // repeated START: SCL low, SDA released, SCL released, SDA low (q 4), then the
                    // first bit. A START from idle enters at q 4.
                    PH_RS: begin
                        q <= q + 1'b1;
                        case (q)
                            3'd0: sda_oe <= 1'b0;
                            3'd1: scl_oe <= 1'b0;
                            3'd3: sda_oe <= 1'b1;
                            3'd5: begin
                                ph <= PH_BIT;
                                q <= '0;
                                bitn <= '0;
                                scl_oe <= 1'b1;
                            end
                            default: ;
                        endcase
                    end

                    PH_BIT: begin
                        q <= q + 1'b1;
                        case (q)
                            3'd0:  // set SDA: data bit, the device's ACK slot, or our ACK/NACK
                                if (bitn != 4'd8) sda_oe <= !reading && !sh[7];
                                else sda_oe <= reading && (2'(idx) + 3'd1 < rlen);
                            3'd1: scl_oe <= 1'b0;
                            3'd2:  // SCL is high: sample
                                if (bitn != 4'd8) sh <= {sh[6:0], reading ? sda_s : 1'b0};
                                else ack <= !sda_s;
                            3'd3:
                                if (bitn != 4'd8) begin
                                    bitn <= bitn + 1'b1;
                                    q <= '0;
                                    scl_oe <= 1'b1;
                                end else begin  // a byte is done; SCL is high
                                    bitn <= '0;
                                    q <= '0;
                                    scl_oe <= 1'b1;
                                    if (!reading && !ack) begin
                                        if (st == ST_WD) dnack <= 1'b1;
                                        else anack <= 1'b1;
                                        ph <= PH_STOP;
                                    end else case (st)
                                        ST_AW:
                                            if (wlen != 0) begin
                                                st <= ST_WD;
                                                idx <= '0;
                                                sh <= wdata[7:0];
                                            end else ph <= PH_STOP;     // a probe
                                        ST_WD:
                                            if (3'(idx) + 3'd1 < wlen) begin
                                                idx <= idx + 1'b1;
                                                sh <= wdata[8 * (idx + 1'b1) +: 8];
                                            end else if (rlen != 0) begin
                                                st <= ST_AR;
                                                sh <= {addr, 1'b1};
                                                if (sep) begin
                                                    restart <= 1'b1;
                                                    ph <= PH_STOP;
                                                end else ph <= PH_RS;
                                            end else ph <= PH_STOP;
                                        ST_AR: begin
                                            st <= ST_RD;
                                            idx <= '0;
                                        end
                                        ST_RD: begin
                                            rd_bytes[8 * idx +: 8] <= sh;
                                            if (3'(idx) + 3'd1 < rlen) idx <= idx + 1'b1;
                                            else ph <= PH_STOP;
                                        end
                                    endcase
                                end
                            default: ;
                        endcase
                    end

                    // STOP: SCL low, SDA low, SCL released, SDA released (q 4), then two quarters
                    // of bus free time.
                    PH_STOP: begin
                        q <= q + 1'b1;
                        case (q)
                            3'd0: sda_oe <= 1'b1;
                            3'd1: scl_oe <= 1'b0;
                            3'd3: sda_oe <= 1'b0;
                            3'd6:
                                if (restart) begin  // SEP: a fresh START for the read
                                    restart <= 1'b0;
                                    ph <= PH_RS;
                                    q <= 3'd4;
                                    sda_oe <= 1'b1;
                                end else begin
                                    ph <= PH_IDLE;
                                    busy <= 1'b0;
                                end
                            default: ;
                        endcase
                    end

                    // bus recovery: SCL pulses with SDA released until SDA reads high
                    PH_RCV: begin
                        q <= q + 1'b1;
                        case (q)
                            3'd1: scl_oe <= 1'b0;
                            3'd2: ack <= sda_s;  // here: SDA has been let go
                            3'd3: begin
                                q <= '0;
                                scl_oe <= 1'b1;
                                if (ack || bitn == 4'd8) ph <= PH_STOP;
                                else bitn <= bitn + 1'b1;
                            end
                            default: ;
                        endcase
                    end

                    default: ;
                endcase
            end
        end
    end

    always_ff @(posedge clk) begin
        rdata <= '0;
        if (bus_rd && sel)
            case (bus_addr)
                A_I2C_DIV:    rdata <= {16'b0, div};
                A_I2C_CMD:    rdata <= cmd;
                A_I2C_WDATA:  rdata <= wdata;
                A_I2C_RDATA:  rdata <= rd_bytes;
                A_I2C_STATUS: rdata <= {22'b0, sda_s, scl_s, 3'b0, stuck, tout, dnack, anack, busy};
                default:      rdata <= '0;
            endcase
    end
endmodule
