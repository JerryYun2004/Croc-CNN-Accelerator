#include "Vcnn_engine.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
#include <stdexcept>
#include <string>
#include <algorithm>
#include <cstdio>

static void require(bool ok,const std::string& s){if(!ok)throw std::runtime_error(s);}
struct Fixture {
  std::array<uint8_t,4096> mem;
  std::array<uint8_t,184> expected;
  uint8_t prediction,shift;
  explicit Fixture(const std::string& path) {
    std::ifstream f(path,std::ios::binary);
    require(bool(f),"Fixture missing: "+path);
    f.read(reinterpret_cast<char*>(mem.data()),4096);
    f.read(reinterpret_cast<char*>(expected.data()),184);
    prediction=uint8_t(f.get());shift=uint8_t(f.get());
    require(bool(f)&&f.peek()==EOF,"Fixture size mismatch");
  }
};
struct Sim {
  Vcnn_engine d;
  std::array<uint8_t,4096> mem;
  std::array<unsigned,4096> writes{};
  int mode=0, pending=0, delay=0, grant_wait=0, inject=0;
  uint32_t response=0, rng=0x820A, held_addr=0, held_data=0, held_be=0, held_we=0;
  bool response_error=false, held=false;
  uint64_t ticks=0,macs=0,reads=0,stores=0,busy_ticks=0;
  uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
  void reset() {
    pending=0;delay=0;held=false;grant_wait=0;inject=0;
    d.clk_i=0;d.rst_ni=0;d.start_i=0;d.clear_i=0;d.conv_shift_i=9;
    d.gnt_i=0;d.rvalid_i=0;d.err_i=0;d.rdata_i=0;d.eval();
    d.clk_i=1;d.eval();d.clk_i=0;d.eval();d.rst_ni=1;d.eval();
    require(!d.busy_o&&!d.done_o&&!d.error_o&&!d.req_o&&d.cycles_o==0,"Reset state");
    macs=reads=stores=busy_ticks=0;writes.fill(0);
  }
  void tick() {
    d.clk_i=0;d.gnt_i=0;d.rvalid_i=0;d.err_i=0;d.eval();
    if(held) require(d.req_o && d.addr_o==held_addr && d.wdata_o==held_data &&
                     d.be_o==held_be && d.we_o==held_we,"Request changed before grant");
    if(pending) {
      require(!d.req_o,"More than one outstanding request");
      if(delay==0) {
        d.rvalid_i=1;d.rdata_i=response;d.err_i=response_error;pending=0;
      } else --delay;
    } else if(d.req_o) {
      require(d.busy_o && (d.addr_o&3)==0 && d.addr_o<=4092,"Invalid memory request");
      if(!held) {
        held_addr=d.addr_o;held_data=d.wdata_o;held_be=d.be_o;held_we=d.we_o;
        grant_wait=mode==2 ? int(random()%4) : 0;held=true;
      }
      if(grant_wait) --grant_wait;
      else {
        d.gnt_i=1;held=false;response=0;
        response_error=(inject==1&&!d.we_o)||(inject==2&&d.we_o);
        if(response_error)inject=0;
        for(unsigned b=0;b<4;++b)response|=uint32_t(mem[d.addr_o+b])<<(8*b);
        if(d.we_o) {
          require(d.addr_o>=0x950 && d.addr_o<0xa08,"Write outside output region");
          require(d.be_o==(d.addr_o>=0x9e0?15:(1u<<((stores%144)%4))),"Bad byte enable");
          if(!response_error)for(unsigned b=0;b<4;++b)if(d.be_o&(1u<<b)) {
            mem[d.addr_o+b]=uint8_t(d.wdata_o>>(8*b));++writes[d.addr_o+b];
          }
          ++stores;
        } else {require(d.be_o==15,"Read strobe");++reads;}
        int latency=mode==0 ? 0 : (mode==1?1:int(random()%4));
        if(latency==0) {d.rvalid_i=1;d.rdata_i=response;d.err_i=response_error;}
        else {pending=1;delay=latency-1;}
      }
    }
    d.eval();if(d.mac_valid_o)++macs;if(d.busy_o)++busy_ticks;
    d.clk_i=1;d.eval();++ticks;
    require(d.cycles_o==busy_ticks,"Cycle counter differs");
    d.clk_i=0;d.eval();
  }
  void start(unsigned shift){d.conv_shift_i=shift;d.start_i=1;busy_ticks=0;tick();d.start_i=0;}
  void finish(){for(unsigned i=0;d.busy_o && i<1500000;++i)tick();require(!d.busy_o,"Watchdog expired");}
  void load(const Fixture& f,int pattern){reset();mem=f.mem;mode=pattern;}
  void check(const Fixture& f) {
    require(d.done_o,"DONE missing");require(d.prediction_o==f.prediction,"Prediction differs");
    for(unsigned i=0;i<184;++i)require(mem[0x950+i]==f.expected[i],"Pooled/logit byte differs at "+std::to_string(i));
    for(unsigned i=0;i<4096;++i) {
      bool output=i>=0x950&&i<0xa08;
      require(output? writes[i]==1 : (mem[i]==f.mem[i]&&writes[i]==0),"Canary or write-count failure");
    }
    require(macs==59040 && reads==120394 && stores==154,"Operation count differs");
  }
};

int main(int argc,char** argv) {
  Verilated::commandArgs(argc,argv);
  if(argc!=3){std::cerr<<"usage: engine FIXTURE_DIRECTORY COUNT\n";return 2;}
  try {
    unsigned count=unsigned(std::stoul(argv[2]));require(count>0,"No fixtures");
    std::vector<Fixture> fixtures;
    for(unsigned i=0;i<count;++i){char file[24];std::snprintf(file,sizeof(file),"/%03u.bin",i);fixtures.emplace_back(std::string(argv[1])+file);}
    Sim s;uint32_t cycle_min=UINT32_MAX,cycle_max=0;uint64_t total=0;
    for(int mode=0;mode<3;++mode)for(unsigned i=0;i<count;++i) {
      s.load(fixtures[i],mode);s.start(fixtures[i].shift);s.finish();
      try{s.check(fixtures[i]);}catch(const std::exception& e){throw std::runtime_error("fixture "+std::to_string(i)+" mode "+std::to_string(mode)+": "+e.what());}
      require(!s.d.error_o,"Unexpected ERROR");
      cycle_min=std::min(cycle_min,s.d.cycles_o);cycle_max=std::max(cycle_max,s.d.cycles_o);
      for(int n=0;n<5;++n)s.tick();require(s.d.done_o,"DONE not sticky");
      s.d.clear_i=1;s.tick();s.d.clear_i=0;require(!s.d.done_o&&!s.d.error_o,"CLEAR failed");
      ++total;
    }
    const Fixture& f=fixtures[0];
    // Invalid shift must not issue a memory transaction.
    s.load(f,0);s.start(31);for(int i=0;i<8;++i)s.tick();
    require(!s.d.busy_o&&!s.d.done_o&&s.d.error_code_o==2&&s.reads==0,"Invalid shift control");
    // Busy commands cannot restart or clear the running operation; shift is latched.
    s.load(f,2);s.start(f.shift);for(int i=0;i<70;++i)s.tick();
    s.d.start_i=1;s.d.clear_i=1;s.d.conv_shift_i=31;s.tick();s.d.start_i=0;s.d.clear_i=0;
    s.finish();s.check(f);require(s.d.error_o&&s.d.error_code_o==3,"Busy command control");
    // Reset an ungranted request, a pending response, a MAC, and an output write.
    for(int kind=0;kind<4;++kind) {
      s.load(f,2);s.start(f.shift);bool found=false;
      for(unsigned i=0;i<1000000;++i){
        if((kind==0&&s.held)||(kind==1&&s.pending)||(kind==2&&s.d.mac_valid_o)||(kind==3&&s.stores>0)){found=true;break;}s.tick();
      }
      require(found,"Reset target not reached");s.reset(); // wrapper also flushes its response
      s.mem=f.mem;s.start(f.shift);s.finish();s.check(f);
    }
    for(int direction=1;direction<=2;++direction) {
      s.load(f,2);s.inject=direction;s.start(f.shift);s.finish();
      require(s.d.error_o&&s.d.error_code_o==4&&!s.d.done_o,"Memory error not caught");
      for(int n=0;n<8;++n)s.tick();require(!s.d.req_o,"Request after abort");
    }
    // Runtime weights/biases can be corrupted: overflow must abort, not wrap.
    s.load(f,0);s.mem[0]=255;s.mem[0x310]=127;
    s.mem[0x374]=255;s.mem[0x375]=255;s.mem[0x376]=255;s.mem[0x377]=127;
    s.start(f.shift);s.finish();require(s.d.error_code_o==5&&!s.d.done_o,"Overflow not caught");
    // Checker controls: corrupt an intermediate, a score and the decision.
    unsigned corruption_controls=0;
    s.load(f,0);s.start(f.shift);s.finish();s.check(f);
    for(unsigned address:{0x950u,0x9e0u}){s.mem[address]^=1;bool caught=false;
      try{s.check(f);}catch(const std::exception&){caught=true;}s.mem[address]^=1;require(caught,"Checker accepted corrupt output");++corruption_controls;}
    Fixture bad=f;bad.prediction^=1;bool caught=false;try{s.check(bad);}catch(const std::exception&){caught=true;}
    require(caught,"Checker accepted corrupt prediction");++corruption_controls;
    // START after DONE must work without asserting reset or CLEAR.
    s.mem=f.mem;s.writes.fill(0);s.macs=s.reads=s.stores=0;
    s.start(f.shift);require(s.d.busy_o&&!s.d.done_o&&!s.d.error_o,"Back-to-back START");
    s.finish();s.check(f);
    // Prolonged backpressure must hold the request and remain resettable.
    s.load(f,2);s.start(f.shift);s.tick();
    while(!s.held)s.tick();s.grant_wait=100;
    uint64_t previous_reads=s.reads;
    for(unsigned i=0;i<100;++i)s.tick();
    require(s.d.busy_o && s.reads==previous_reads,"Long grant stall");s.reset();
    std::cout<<"{\"status\":\"ENGINE_PASS\",\"fixtures\":"<<count<<",\"full_inferences\":"<<total
      <<",\"stall_modes\":3,\"cycles_min\":"<<cycle_min<<",\"cycles_max\":"<<cycle_max
      <<",\"macs_per_inference\":59040,\"reset_controls\":4,\"memory_error_controls\":2,\"busy_command_control\":true,\"invalid_shift_control\":true,\"overflow_control\":true,\"back_to_back_control\":true,\"long_stall_control\":true,\"output_corruption_controls\":"<<corruption_controls<<"}\n";
    s.d.final();return 0;
  }catch(const std::exception& e){std::cerr<<"RTL_TEST_FAILED: "<<e.what()<<"\n";return 1;}
}
