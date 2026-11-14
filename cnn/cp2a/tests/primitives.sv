module primitives (
  input logic signed [7:0] a_i, b_i,
  input logic signed [31:0] acc_i,
  input logic [4:0] shift_i,
  output logic signed [31:0] sum_o,
  output logic overflow_o,
  output logic [7:0] value_o
);
  cnn_mac u_mac(.*);
  cnn_quant u_quant(.*);
endmodule
