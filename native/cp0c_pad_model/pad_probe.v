// One-cell timing fixture only. No physical Croc netlist is modified.
module pad_probe(input data_i, input en_i, inout pad_io, output recv_o);
  sg13cmos5l_IOPadInOut30mA u_pad (
    .c2p(data_i), .c2p_en(en_i), .pad(pad_io), .p2c(recv_o)
  );
endmodule
