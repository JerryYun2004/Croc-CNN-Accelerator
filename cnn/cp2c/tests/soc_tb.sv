`timescale 1ns/1ps
module cp2c_soc_tb;
  import tb_croc_pkg::*;
  logic rst_n, clk, ref_clk, tck, trst_n, tms, tdi, tdo, rx, tx;
  logic [15:0] gpio_in, gpio_out, gpio_en;
  croc_vip #(.GpioCount(16)) vip (
    .rst_no(rst_n), .sys_clk_o(clk), .ref_clk_o(ref_clk),
    .jtag_tck_o(tck), .jtag_trst_no(trst_n), .jtag_tms_o(tms),
    .jtag_tdi_o(tdi), .jtag_tdo_i(tdo), .uart_rx_o(rx), .uart_tx_i(tx),
    .gpio_out_en_i(gpio_en), .gpio_out_i(gpio_out), .gpio_in_o(gpio_in)
  );
  croc_soc #(.GpioCount(16)) dut (
    .clk_i(clk), .rst_ni(rst_n), .ref_clk_i(ref_clk), .testmode_i(1'b0), .status_o(),
    .jtag_tck_i(tck), .jtag_trst_ni(trst_n), .jtag_tms_i(tms),
    .jtag_tdi_i(tdi), .jtag_tdo_o(tdo), .uart_rx_i(rx), .uart_tx_o(tx),
    .gpio_i(gpio_in), .gpio_o(gpio_out), .gpio_out_en_o(gpio_en)
  );
  // Observation only: no force/deposit or alternate memory/CPU path.
  longint unsigned cpu_loads=0, cpu_stores=0, cpu_fetches=0;
  bit running=0;
  always @(posedge clk) if (running) begin
    if (dut.i_croc.core_instr_obi_req.req && dut.i_croc.core_instr_obi_rsp.gnt) cpu_fetches++;
  end
  // The full CPU data interface is checked by the software return code;
  // crossbar-side traffic is counted to demonstrate actual CNN transactions.
  always @(posedge clk) if (running && dut.user_sbr_obi_req.req && dut.user_sbr_obi_rsp.gnt) begin
    if (dut.user_sbr_obi_req.a.we) cpu_stores++; else cpu_loads++;
  end
  string binary_path;
  bit [31:0] data, report_addr, v[16];
  initial begin
    if (!$value$plusargs("binary=%s",binary_path)) $fatal(1,"Missing binary");
    if (!$value$plusargs("report=%h",report_addr)) $fatal(1,"Missing report address");
    #1050ns;
    vip.jtag_init();
    vip.jtag_load_hex(binary_path);
    // JTAG traffic stops before wakeup. The CPU performs all CNN accesses.
    running=1;
    vip.jtag_write_reg32(ClintBaseAddr,32'h1);
    // Observe EOC without concurrent JTAG polling or bus contention.
    wait (dut.i_croc.soc_ctrl_obi_req.req && dut.i_croc.soc_ctrl_obi_rsp.gnt &&
          dut.i_croc.soc_ctrl_obi_req.a.we &&
          dut.i_croc.soc_ctrl_obi_req.a.addr == CoreStatusAddr);
    data=dut.i_croc.soc_ctrl_obi_req.a.wdata;
    if (data!=1) $fatal(1,"CP2C_FIRMWARE_FAIL status=%08x",data);
    running=0;
    repeat(20) @(posedge clk);
    for (int i=0;i<16;i++) vip.jtag_read_reg32(report_addr+4*i,v[i]);
    if (v[7]!=32'h43503243 || v[15]!=32'h43503243 || cpu_fetches==0 || cpu_stores<792)
      $fatal(1,"Incomplete CPU evidence");
    for (int r=0;r<2;r++)
      $display("CP2C_MEASUREMENT %0d %0d %0d %0d %0d %0d %0d %0d",r,
        v[8*r],v[8*r+1],v[8*r+2],v[8*r+3],v[8*r+4],v[8*r+5],v[8*r+6]);
    $display("CP2C_SOC_PASS fetches=%0d cnn_reads=%0d cnn_writes=%0d",cpu_fetches,cpu_loads,cpu_stores);
    $finish;
  end
  initial begin
    #100ms;
    $fatal(1,"CP2C timeout");
  end
endmodule
