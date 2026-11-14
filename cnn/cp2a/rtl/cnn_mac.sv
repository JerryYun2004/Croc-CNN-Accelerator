module cnn_mac (
  input  logic signed [7:0] a_i, b_i,
  input  logic signed [31:0] acc_i,
  output logic signed [31:0] sum_o,
  output logic overflow_o
);
  logic signed [15:0] product;
  logic signed [32:0] wide_sum;
  assign product = a_i * b_i;
  assign wide_sum = {acc_i[31], acc_i} + {{17{product[15]}}, product};
  assign sum_o = wide_sum[31:0];
  assign overflow_o = wide_sum[32] != wide_sum[31];
endmodule
