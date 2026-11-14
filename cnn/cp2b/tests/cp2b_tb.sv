// Test-only delay/fault bridge, absent from cnn_peripheral and synthesis.
module cp2b_tb (
  input logic clk_i, rst_ni, req_i, we_i,
  input logic [31:0] addr_i, wdata_i,
  input logic [3:0] be_i,
  input logic [2:0] aid_i,
  output logic gnt_o, rvalid_o, err_o,
  output logic [31:0] rdata_o,
  output logic [2:0] rid_o,
  input logic direct_i, allow_grant_i, allow_response_i, fault_i,
  output logic local_req_o, local_gnt_o, local_we_o, local_rvalid_o,
  output logic [11:0] local_addr_o,
  output logic [31:0] local_wdata_o,
  output logic [3:0] local_be_o
);
  logic local_err;
  logic [31:0] local_rdata;
  logic sram_req, sram_gnt, sram_rvalid, sram_err;
  logic [31:0] sram_rdata, held_data;
  logic held_err, held_fault;
  logic [1:0] state;
  cnn_obi u_obi (
    .clk_i, .rst_ni, .req_i, .we_i, .addr_i, .wdata_i, .be_i, .aid_i,
    .gnt_o, .rvalid_o, .err_o, .rdata_o, .rid_o,
    .mem_req_o(local_req_o), .mem_we_o(local_we_o), .mem_addr_o(local_addr_o),
    .mem_wdata_o(local_wdata_o), .mem_be_o(local_be_o),
    .mem_gnt_i(local_gnt_o), .mem_rvalid_i(local_rvalid_o),
    .mem_err_i(local_err), .mem_rdata_i(local_rdata)
  );
  assign local_gnt_o = direct_i ? sram_gnt : (rst_ni && state == 0 && allow_grant_i && sram_gnt);
  assign local_rvalid_o = direct_i ? sram_rvalid : (rst_ni && state == 2 && allow_response_i);
  assign local_rdata = direct_i ? sram_rdata : held_data;
  assign local_err = direct_i ? sram_err : held_err;
  assign sram_req = direct_i ? local_req_o : (local_req_o && local_gnt_o && !fault_i);
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin state <= 0; held_data <= 0; held_err <= 0; held_fault <= 0; end
    else if (!direct_i) begin
      case (state)
        0: if (local_req_o && local_gnt_o) begin state <= 1; held_fault <= fault_i; end
        1: if (sram_rvalid || held_fault) begin
          held_data <= held_fault ? 32'd0 : sram_rdata;
          held_err <= held_fault || sram_err; state <= 2;
        end
        2: if (allow_response_i) state <= 0;
        default: state <= 0;
      endcase
    end
  end
  cnn_sram_4k u_sram (
    .clk_i, .rst_ni, .req_i(sram_req), .we_i(local_we_o), .addr_i(local_addr_o),
    .wdata_i(local_wdata_o), .be_i(local_be_o), .gnt_o(sram_gnt),
    .rvalid_o(sram_rvalid), .err_o(sram_err), .rdata_o(sram_rdata)
  );
endmodule
