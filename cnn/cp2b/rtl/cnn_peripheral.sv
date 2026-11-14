module cnn_peripheral #(
  parameter integer IdWidth = 3
) (
  input logic clk_i, rst_ni, req_i, we_i,
  input logic [31:0] addr_i, wdata_i,
  input logic [3:0] be_i,
  input logic [IdWidth-1:0] aid_i,
  output logic gnt_o, rvalid_o, err_o,
  output logic [31:0] rdata_o,
  output logic [IdWidth-1:0] rid_o
);
  logic mem_req, mem_we, mem_gnt, mem_rvalid, mem_err;
  logic [11:0] mem_addr;
  logic [31:0] mem_wdata, mem_rdata;
  logic [3:0] mem_be;
  cnn_obi #(.IdWidth(IdWidth)) u_obi (
    .clk_i, .rst_ni, .req_i, .we_i, .addr_i, .wdata_i, .be_i, .aid_i,
    .gnt_o, .rvalid_o, .err_o, .rdata_o, .rid_o,
    .mem_req_o(mem_req), .mem_we_o(mem_we), .mem_addr_o(mem_addr),
    .mem_wdata_o(mem_wdata), .mem_be_o(mem_be),
    .mem_gnt_i(mem_gnt), .mem_rvalid_i(mem_rvalid), .mem_err_i(mem_err), .mem_rdata_i(mem_rdata)
  );
  cnn_sram_4k u_sram (
    .clk_i, .rst_ni, .req_i(mem_req), .we_i(mem_we), .addr_i(mem_addr),
    .wdata_i(mem_wdata), .be_i(mem_be), .gnt_o(mem_gnt), .rvalid_o(mem_rvalid),
    .err_o(mem_err), .rdata_o(mem_rdata)
  );
endmodule
