// Standalone CP1 engine. Memory is external; see docs/INTERFACE.md.
// One outstanding transaction, including writes, with grant and response.
module cnn_engine (
  input logic clk_i, rst_ni,
  input logic start_i, clear_i,
  input logic [4:0] conv_shift_i,
  output logic busy_o, done_o, error_o,
  output logic [2:0] error_code_o,
  output logic [3:0] prediction_o,
  output logic [31:0] cycles_o,
  output logic req_o,
  input logic gnt_i,
  output logic we_o,
  output logic [11:0] addr_o,
  output logic [31:0] wdata_o,
  output logic [3:0] be_o,
  input logic rvalid_i,
  input logic [31:0] rdata_i,
  input logic err_i,
  output logic mac_valid_o
);
  typedef enum logic [3:0] {
    IDLE, BIAS_REQ, BIAS_WAIT, A_REQ, A_WAIT, W_REQ, W_WAIT,
    MAC, RESULT, STORE_REQ, STORE_WAIT, FINISH
  } state_t;
  state_t state;
  logic fc;
  logic [4:0] shift;
  logic [2:0] py, px, ky, kx;
  logic [1:0] oc, dy, dx;
  logic [7:0] fi;
  logic [3:0] cls, best_cls;
  logic signed [31:0] acc, best_score, mac_sum;
  logic signed [7:0] activation, weight;
  logic [7:0] pool_max, quantized;
  logic overflow, complete;
  logic [11:0] byte_addr;
  logic [7:0] read_byte;
  logic [11:0] row_index, col_index, pool_index;
  cnn_mac u_mac(.a_i(activation), .b_i(weight), .acc_i(acc),
                .sum_o(mac_sum), .overflow_o(overflow));
  cnn_quant u_quant(.acc_i(acc), .shift_i(shift), .value_o(quantized));
  assign row_index = ({9'd0, py} << 2) + {10'd0, dy} + {9'd0, ky};
  assign col_index = ({9'd0, px} << 2) + {10'd0, dx} + {9'd0, kx};
  assign pool_index = (({9'd0, py} * 12'd6 + {9'd0, px}) << 2) + {10'd0, oc};
  assign mac_valid_o = state == MAC;
  assign complete = rvalid_i && ((req_o && gnt_i) ||
                    state == BIAS_WAIT || state == A_WAIT ||
                    state == W_WAIT || state == STORE_WAIT);
  always_comb begin
    req_o = 1'b0;
    we_o = 1'b0;
    byte_addr = 12'd0;
    wdata_o = 32'd0;
    be_o = 4'b1111;
    case (state)
      BIAS_REQ, BIAS_WAIT: begin
        req_o = state == BIAS_REQ;
        byte_addr = fc ? (12'h924 + ({8'd0, cls} << 2)) :
                         (12'h374 + ({10'd0, oc} << 2));
      end
      A_REQ, A_WAIT: begin
        req_o = state == A_REQ;
        byte_addr = fc ? (12'h950 + {4'd0, fi}) : (row_index * 12'd28 + col_index);
      end
      W_REQ, W_WAIT: begin
        req_o = state == W_REQ;
        byte_addr = fc ? (12'h384 + {8'd0, cls} * 12'd144 + {4'd0, fi}) :
          (12'h310 + {10'd0, oc} * 12'd25 + {9'd0, ky} * 12'd5 + {9'd0, kx});
      end
      STORE_REQ, STORE_WAIT: begin
        req_o = state == STORE_REQ;
        we_o = 1'b1;
        byte_addr = fc ? (12'h9e0 + ({8'd0, cls} << 2)) : (12'h950 + pool_index);
        wdata_o = fc ? acc : ({24'd0, pool_max} << {byte_addr[1:0], 3'b000});
        be_o = fc ? 4'b1111 : (4'b0001 << byte_addr[1:0]);
      end
      default: begin end
    endcase
    addr_o = {byte_addr[11:2], 2'b00};
    case (byte_addr[1:0])
      2'd0: read_byte = rdata_i[7:0];
      2'd1: read_byte = rdata_i[15:8];
      2'd2: read_byte = rdata_i[23:16];
      default: read_byte = rdata_i[31:24];
    endcase
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state <= IDLE; fc <= 0; shift <= 9;
      py <= 0; px <= 0; ky <= 0; kx <= 0; oc <= 0; dy <= 0; dx <= 0;
      fi <= 0; cls <= 0; best_cls <= 0; acc <= 0; best_score <= 0;
      activation <= 0; weight <= 0; pool_max <= 0;
      busy_o <= 0; done_o <= 0; error_o <= 0; error_code_o <= 0;
      prediction_o <= 0; cycles_o <= 0;
    end else begin
      if (busy_o) begin
        if (cycles_o != 32'hffffffff) cycles_o <= cycles_o + 32'd1;
        if (start_i || clear_i) begin error_o <= 1; error_code_o <= 3'd3; end
      end
      if (complete && err_i) begin
        state <= IDLE; busy_o <= 0; done_o <= 0;
        error_o <= 1; error_code_o <= 3'd4;
      end else case (state)
        IDLE: begin
          if (clear_i) begin done_o <= 0; error_o <= 0; error_code_o <= 0; end
          if (start_i) begin
            done_o <= 0; error_o <= 0; error_code_o <= 0;
            prediction_o <= 0; cycles_o <= 0;
            if (conv_shift_i == 5'd31) begin error_o <= 1; error_code_o <= 3'd2; end
            else begin
              busy_o <= 1; state <= BIAS_REQ; shift <= conv_shift_i; fc <= 0;
              py <= 0; px <= 0; oc <= 0; dy <= 0; dx <= 0; ky <= 0; kx <= 0;
              fi <= 0; cls <= 0; best_cls <= 0; best_score <= 0; pool_max <= 0;
            end
          end
        end
        BIAS_REQ, BIAS_WAIT: begin
          if (complete) begin acc <= $signed(rdata_i); state <= A_REQ; end
          else if (gnt_i && state == BIAS_REQ) state <= BIAS_WAIT;
        end
        A_REQ, A_WAIT: begin
          if (complete) begin
            activation <= fc ? $signed(read_byte) : $signed({1'b0, read_byte[7:1]});
            state <= W_REQ;
          end else if (gnt_i && state == A_REQ) state <= A_WAIT;
        end
        W_REQ, W_WAIT: begin
          if (complete) begin weight <= $signed(read_byte); state <= MAC; end
          else if (gnt_i && state == W_REQ) state <= W_WAIT;
        end
        MAC: begin
          if (overflow) begin
            error_o <= 1; error_code_o <= 3'd5; busy_o <= 0; done_o <= 0; state <= IDLE;
          end else begin
            acc <= mac_sum;
            if (fc) begin
              if (fi == 8'd143) state <= RESULT;
              else begin fi <= fi + 8'd1; state <= A_REQ; end
            end else if (kx == 3'd4) begin
              kx <= 0;
              if (ky == 3'd4) begin ky <= 0; state <= RESULT; end
              else begin ky <= ky + 3'd1; state <= A_REQ; end
            end else begin kx <= kx + 3'd1; state <= A_REQ; end
          end
        end
        RESULT: begin
          if (fc) begin
            if (cls == 0 || acc > best_score) begin best_score <= acc; best_cls <= cls; end
            state <= STORE_REQ;
          end else begin
            if (quantized > pool_max) pool_max <= quantized;
            if (dx == 2'd3) begin
              dx <= 0;
              if (dy == 2'd3) begin dy <= 0; state <= STORE_REQ; end
              else begin dy <= dy + 2'd1; state <= BIAS_REQ; end
            end else begin dx <= dx + 2'd1; state <= BIAS_REQ; end
          end
        end
        STORE_REQ, STORE_WAIT: begin
          if (complete) begin
            if (fc) begin
              if (cls == 4'd9) state <= FINISH;
              else begin cls <= cls + 4'd1; fi <= 0; state <= BIAS_REQ; end
            end else begin
              pool_max <= 0; state <= BIAS_REQ;
              if (oc == 2'd3) begin
                oc <= 0;
                if (px == 3'd5) begin
                  px <= 0;
                  if (py == 3'd5) begin fc <= 1; fi <= 0; cls <= 0; end
                  else py <= py + 3'd1;
                end else px <= px + 3'd1;
              end else oc <= oc + 2'd1;
            end
          end else if (gnt_i && state == STORE_REQ) state <= STORE_WAIT;
        end
        FINISH: begin prediction_o <= best_cls; done_o <= 1; busy_o <= 0; state <= IDLE; end
        default: state <= IDLE;
      endcase
    end
  end
endmodule
