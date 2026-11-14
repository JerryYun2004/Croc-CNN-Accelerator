module user_tb (
  input logic clk_i, rst_ni, req_i, we_i,
  input logic [31:0] addr_i, wdata_i,
  input logic [3:0] be_i,
  input logic [2:0] aid_i,
  output logic gnt_o, rvalid_o, err_o,
  output logic [31:0] rdata_o,
  output logic [2:0] rid_o,
  output logic side_effect_o
);
  croc_pkg::sbr_obi_req_t request;
  croc_pkg::sbr_obi_rsp_t response;
  croc_pkg::mgr_obi_req_t manager;
  logic [3:0] interrupts;
  assign request = '{a:'{addr:addr_i,we:we_i,be:be_i,wdata:wdata_i,aid:aid_i,a_optional:1'b0},req:req_i};
  assign gnt_o=response.gnt;
  assign rvalid_o=response.rvalid;
  assign rdata_o=response.r.rdata;
  assign rid_o=response.r.rid;
  assign err_o=response.r.err;
  assign side_effect_o = (|manager) || (|interrupts) || response.r.r_optional;
  user_domain u_domain (
    .clk_i, .ref_clk_i(clk_i), .rst_ni, .testmode_i(1'b0),
    .user_sbr_obi_req_i(request), .user_sbr_obi_rsp_o(response),
    .user_mgr_obi_req_o(manager), .user_mgr_obi_rsp_i('0),
    .gpio_in_sync_i(16'd0), .interrupts_o(interrupts)
  );
endmodule
