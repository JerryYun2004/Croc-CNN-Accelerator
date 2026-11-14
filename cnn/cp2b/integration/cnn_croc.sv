// Typed Croc subordinate adapter. No manager/DMA/IRQ interface is needed.
module cnn_croc (
  input logic clk_i, rst_ni,
  input croc_pkg::sbr_obi_req_t obi_req_i,
  output croc_pkg::sbr_obi_rsp_t obi_rsp_o
);
  assign obi_rsp_o.r.r_optional = 1'b0;
  cnn_peripheral #(.IdWidth(croc_pkg::SbrObiCfg.IdWidth)) u_peripheral (
    .clk_i, .rst_ni, .req_i(obi_req_i.req), .we_i(obi_req_i.a.we),
    .addr_i(obi_req_i.a.addr), .wdata_i(obi_req_i.a.wdata), .be_i(obi_req_i.a.be),
    .aid_i(obi_req_i.a.aid), .gnt_o(obi_rsp_o.gnt), .rvalid_o(obi_rsp_o.rvalid),
    .err_o(obi_rsp_o.r.err), .rdata_o(obi_rsp_o.r.rdata), .rid_o(obi_rsp_o.r.rid)
  );
  // a_optional is a one-bit unused placeholder in the frozen Croc configuration.
  logic unused_optional;
  assign unused_optional = obi_req_i.a.a_optional;
endmodule
