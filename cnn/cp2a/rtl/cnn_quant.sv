// Rounding followed by ReLU: every negative input maps to zero.
// The extra bit keeps INT32_MAX + rounding offset from overflowing.
module cnn_quant (
  input logic signed [31:0] acc_i,
  input logic [4:0] shift_i,
  output logic [7:0] value_o
);
  logic [32:0] offset, rounded;
  always_comb begin
    offset = 33'd0;
    if (shift_i != 0) offset = 33'd1 << (shift_i - 5'd1);
    rounded = ({1'b0, acc_i} + offset) >> shift_i;
    if (acc_i[31]) value_o = 8'd0;
    else if (rounded > 33'd127) value_o = 8'd127;
    else value_o = rounded[7:0];
  end
endmodule
