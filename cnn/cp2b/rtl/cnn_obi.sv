// CP2B: one-outstanding OBI subordinate and exclusive local-memory ownership.
module cnn_obi #(
  parameter integer IdWidth = 3,
  parameter logic [31:0] CtrlBase = 32'h2000_1000,
  parameter logic [31:0] BufferBase = 32'h2000_2000
) (
  input logic clk_i, rst_ni,
  input logic req_i, we_i,
  input logic [31:0] addr_i, wdata_i,
  input logic [3:0] be_i,
  input logic [IdWidth-1:0] aid_i,
  output logic gnt_o, rvalid_o, err_o,
  output logic [31:0] rdata_o,
  output logic [IdWidth-1:0] rid_o,
  output logic mem_req_o, mem_we_o,
  output logic [11:0] mem_addr_o,
  output logic [31:0] mem_wdata_o,
  output logic [3:0] mem_be_o,
  input logic mem_gnt_i, mem_rvalid_i, mem_err_i,
  input logic [31:0] mem_rdata_i
);
  typedef enum logic [1:0] {IDLE, MEM_REQ, MEM_WAIT, RESPONSE} state_t;
  state_t state;
  logic [11:0] cpu_addr;
  logic [31:0] cpu_wdata, response_data;
  logic [3:0] cpu_be;
  logic cpu_we, response_error;
  logic [IdWidth-1:0] response_id;
  logic [4:0] conv_shift;
  logic sticky_error;
  logic [2:0] sticky_code, visible_code;
  logic ctrl_hit, buffer_hit, access_error, accept, start, clear, set_shift;
  logic [2:0] access_code;
  logic [31:0] csr_data;
  logic engine_busy, engine_done, engine_error, engine_req, engine_we;
  logic [2:0] engine_code;
  logic [3:0] prediction, engine_be;
  logic [31:0] cycles, engine_wdata;
  logic [11:0] engine_addr;
  logic cpu_complete;
  logic unused_mac_valid;
  // OBI grant is independent of req. No response-ready signal (UseRReady=0).
  assign gnt_o = rst_ni && state == IDLE;
  assign accept = req_i && gnt_o;
  assign rvalid_o = rst_ni && state == RESPONSE;
  assign rid_o = response_id;
  assign rdata_o = response_data;
  assign err_o = response_error;
  assign ctrl_hit = addr_i[31:12] == CtrlBase[31:12];
  assign buffer_hit = addr_i[31:12] == BufferBase[31:12];
  assign visible_code = sticky_error ? sticky_code : engine_code;
  always_comb begin
    csr_data = 0;
    access_error = 0;
    access_code = 3'd1;
    start = 0; clear = 0; set_shift = 0;
    if (addr_i[1:0] != 0) access_error = 1;
    else if (buffer_hit) begin
      if (engine_busy) begin access_error = 1; access_code = 3'd3; end
    end else if (ctrl_hit) begin
      case (addr_i[11:0])
        12'h000: begin csr_data = 32'h434e4e31; access_error = we_i; end
        12'h004: begin csr_data = 32'h00010000; access_error = we_i; end
        12'h008: begin
          if (we_i) begin
            if (be_i != 4'hf || wdata_i[31:2] != 0) access_error = 1;
            else if (engine_busy && wdata_i[1:0] != 0) begin
              access_error = 1; access_code = 3'd3;
            end else begin start = accept && wdata_i[0]; clear = accept && wdata_i[1]; end
          end
        end
        12'h00c: begin
          csr_data = {29'd0, (sticky_error || engine_error), engine_done, engine_busy};
          access_error = we_i;
        end
        12'h010: begin csr_data = {29'd0, visible_code}; access_error = we_i; end
        12'h014: begin csr_data = cycles; access_error = we_i; end
        12'h018: begin
          csr_data = {27'd0, conv_shift};
          if (we_i) begin
            if (be_i != 4'hf) access_error = 1;
            else if (engine_busy) begin access_error = 1; access_code = 3'd3; end
            else if (wdata_i > 32'd30) begin access_error = 1; access_code = 3'd2; end
            else set_shift = accept;
          end
        end
        12'h01c: begin csr_data = {28'd0, prediction}; access_error = we_i; end
        default: access_error = 1;
      endcase
    end else access_error = 1;
  end

  // CPU memory operations finish before any later START can be accepted.
  // While BUSY only the engine can reach local memory; CSR polls use no SRAM port.
  assign mem_req_o = rst_ni && (engine_busy ? engine_req : state == MEM_REQ);
  assign mem_we_o = engine_busy ? engine_we : cpu_we;
  assign mem_addr_o = engine_busy ? engine_addr : cpu_addr;
  assign mem_wdata_o = engine_busy ? engine_wdata : cpu_wdata;
  assign mem_be_o = engine_busy ? engine_be : cpu_be;
  assign cpu_complete = !engine_busy && mem_rvalid_i &&
                        ((state == MEM_REQ && mem_gnt_i) || state == MEM_WAIT);
  cnn_engine u_engine (
    .clk_i, .rst_ni, .start_i(start), .clear_i(clear), .conv_shift_i(conv_shift),
    .busy_o(engine_busy), .done_o(engine_done), .error_o(engine_error),
    .error_code_o(engine_code), .prediction_o(prediction), .cycles_o(cycles),
    .req_o(engine_req), .gnt_i(mem_gnt_i && engine_busy), .we_o(engine_we),
    .addr_o(engine_addr), .wdata_o(engine_wdata), .be_o(engine_be),
    .rvalid_i(mem_rvalid_i && engine_busy), .rdata_i(mem_rdata_i),
    .err_i(mem_err_i), .mac_valid_o(unused_mac_valid)
  );
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state <= IDLE; cpu_addr <= 0; cpu_wdata <= 0; cpu_be <= 0; cpu_we <= 0;
      response_data <= 0; response_error <= 0; response_id <= 0;
      conv_shift <= 9; sticky_error <= 0; sticky_code <= 0;
    end else begin
      // First error is retained until an accepted idle START/CLEAR.
      if (!sticky_error) begin
        if (engine_error) begin sticky_error <= 1; sticky_code <= engine_code; end
        else if (accept && access_error) begin sticky_error <= 1; sticky_code <= access_code; end
        else if (cpu_complete && mem_err_i) begin sticky_error <= 1; sticky_code <= 4; end
      end
      if (start || clear) begin sticky_error <= 0; sticky_code <= 0; end
      if (set_shift) conv_shift <= wdata_i[4:0];
      case (state)
        IDLE: if (accept) begin
          response_id <= aid_i;
          response_error <= access_error;
          response_data <= access_error ? 32'd0 : csr_data;
          if (buffer_hit && !access_error) begin
            cpu_addr <= addr_i[11:0]; cpu_wdata <= wdata_i; cpu_be <= be_i; cpu_we <= we_i;
            state <= MEM_REQ;
          end else state <= RESPONSE;
        end
        MEM_REQ, MEM_WAIT: begin
          if (cpu_complete) begin
            response_data <= (cpu_we || mem_err_i) ? 32'd0 : mem_rdata_i;
            response_error <= mem_err_i; state <= RESPONSE;
          end else if (state == MEM_REQ && mem_gnt_i) state <= MEM_WAIT;
        end
        RESPONSE: state <= IDLE;
        default: state <= IDLE;
      endcase
    end
  end
endmodule
