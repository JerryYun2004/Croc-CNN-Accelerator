// Two physical 512x32 single-port IHP macros; no inferred/register-array memory.
// One registered response per grant, including writes. No reset of SRAM contents.
module cnn_sram_4k (
  input logic clk_i, rst_ni, req_i, we_i,
  input logic [11:0] addr_i,
  input logic [31:0] wdata_i,
  input logic [3:0] be_i,
  output logic gnt_o, rvalid_o, err_o,
  output logic [31:0] rdata_o
);
  logic response_valid, response_bank, response_we, response_error, fire;
  logic [31:0] bitmask;
  wire [31:0] data0, data1;
  assign bitmask = {{8{be_i[3]}}, {8{be_i[2]}}, {8{be_i[1]}}, {8{be_i[0]}}};
  assign gnt_o = rst_ni && !response_valid;
  assign fire = req_i && gnt_o;
  assign rvalid_o = rst_ni && response_valid;
  assign err_o = response_error;
  assign rdata_o = (response_we || response_error) ? 32'd0 : (response_bank ? data1 : data0);
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin response_valid <= 0; response_bank <= 0; response_we <= 0; response_error <= 0; end
    else begin
      response_valid <= fire;
      if (fire) begin
        response_bank <= addr_i[11]; response_we <= we_i; response_error <= |addr_i[1:0];
      end
    end
  end
  RM_IHPSG13_1P_512x32_c2_bm_bist u_bank0 (
    .A_CLK(clk_i), .A_MEN(fire && !addr_i[11] && addr_i[1:0] == 0),
    .A_WEN(we_i), .A_REN(!we_i), .A_ADDR(addr_i[10:2]),
    .A_DIN(wdata_i), .A_DOUT(data0), .A_BM(bitmask), .A_DLY(1'b0),
    .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
    .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(9'd0),
    .A_BIST_DIN(32'd0), .A_BIST_BM(32'd0)
  );
  RM_IHPSG13_1P_512x32_c2_bm_bist u_bank1 (
    .A_CLK(clk_i), .A_MEN(fire && addr_i[11] && addr_i[1:0] == 0),
    .A_WEN(we_i), .A_REN(!we_i), .A_ADDR(addr_i[10:2]),
    .A_DIN(wdata_i), .A_DOUT(data1), .A_BM(bitmask), .A_DLY(1'b0),
    .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
    .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(9'd0),
    .A_BIST_DIN(32'd0), .A_BIST_BM(32'd0)
  );
endmodule
