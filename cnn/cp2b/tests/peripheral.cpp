#include "Vcp2b_tb.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdio>

static constexpr uint32_t C=0x20001000, B=0x20002000;
static void check(bool ok,const std::string& s){if(!ok)throw std::runtime_error(s);}
struct Fixture {
  std::array<uint8_t,4096> mem;
  std::array<uint8_t,184> expected;
  uint8_t prediction,shift;
  explicit Fixture(const std::string& path){
    std::ifstream f(path,std::ios::binary);check(bool(f),"Fixture missing");
    f.read(reinterpret_cast<char*>(mem.data()),4096);
    f.read(reinterpret_cast<char*>(expected.data()),184);
    prediction=uint8_t(f.get());shift=uint8_t(f.get());
    check(bool(f)&&f.peek()==EOF,"Fixture length");
  }
};
static uint32_t word(const std::array<uint8_t,4096>& a,unsigned p){
  uint32_t v=0;for(unsigned i=0;i<4;++i)v|=uint32_t(a[p+i])<<(i*8);return v;
}
struct Response {uint32_t data;bool err;unsigned id;};
struct Sim {
  Vcp2b_tb d;
  uint64_t ticks=0,accepts=0,responses=0,reads=0,writes=0,full=0;
  uint32_t rng=0xc02b1234;int mode=0,force_grant=-1,force_response=-1;
  bool pending=false,held=false;unsigned pending_id=0;
  uint32_t held_addr=0,held_data=0;unsigned held_we=0,held_be=0;
  bool last_grant=false,last_response=false,local_fire=false;Response last{};
  uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
  void reset(){
    d.clk_i=0;d.rst_ni=0;d.req_i=0;d.we_i=0;d.addr_i=0;d.wdata_i=0;d.be_i=15;d.aid_i=0;
    d.direct_i=mode==0;d.allow_grant_i=1;d.allow_response_i=1;d.fault_i=0;
    d.eval();check(!d.gnt_o&&!d.rvalid_o&&!d.local_req_o,"Reset outputs");
    d.clk_i=1;d.eval();d.clk_i=0;d.eval();d.rst_ni=1;d.eval();
    pending=false;held=false;force_grant=force_response=-1;
    for(int i=0;i<4;++i)tick();
  }
  void tick(){
    d.clk_i=0;d.direct_i=mode==0;
    d.allow_grant_i=force_grant<0?(mode==0||random()%4!=0):force_grant;
    d.allow_response_i=force_response<0?(mode==0||random()%4!=0):force_response;
    d.eval();
    if(held)check(d.local_req_o&&d.local_addr_o==held_addr&&d.local_wdata_o==held_data&&
                  d.local_we_o==held_we&&d.local_be_o==held_be,"Local request changed before grant");
    held=d.local_req_o&&!d.local_gnt_o;
    if(held){held_addr=d.local_addr_o;held_data=d.local_wdata_o;held_we=d.local_we_o;held_be=d.local_be_o;}
    last_grant=d.req_i&&d.gnt_o;last_response=d.rvalid_o;
    check(!(d.gnt_o&&d.rvalid_o),"Grant during response: more than one slot");
    if(last_grant){check(!pending,"Second outstanding OBI request");pending=true;pending_id=d.aid_i;++accepts;}
    if(last_response){
      check(pending&&!last_grant,"Unexpected or zero-latency OBI response");
      last={d.rdata_o,bool(d.err_o),d.rid_o};check(last.id==pending_id,"AID/RID mismatch");pending=false;++responses;
    }
    local_fire=d.local_req_o&&d.local_gnt_o;
    if(local_fire){if(d.local_we_o)++writes;else ++reads;}
    d.clk_i=1;d.eval();d.clk_i=0;d.eval();++ticks;
  }
  Response transaction(uint32_t a,bool w=false,uint32_t v=0,unsigned be=15,int id=-1){
    check(!pending,"Transaction issued with outstanding request");
    d.req_i=1;d.we_i=w;d.addr_i=a;d.wdata_i=v;d.be_i=be;d.aid_i=id<0?random()%8:unsigned(id);
    unsigned n=0;do{tick();check(++n<10000,"OBI grant watchdog");}while(!last_grant);
    d.req_i=0; // Fields may change immediately after grant: slave must have latched them.
    d.addr_i=random();d.wdata_i=random();d.we_i=!w;d.be_i=random()%16;d.aid_i=random()%8;
    n=0;do{tick();check(++n<10000,"OBI response watchdog");}while(!last_response);
    return last;
  }
  uint32_t read(uint32_t a){auto r=transaction(a);check(!r.err,"Unexpected read error at "+std::to_string(a));return r.data;}
  void write(uint32_t a,uint32_t v,unsigned be=15){check(!transaction(a,true,v,be).err,"Unexpected write error");}
  void bad(uint32_t a,bool w,uint32_t v,unsigned be=15){check(transaction(a,w,v,be).err,"Missing OBI error");}
  void clear(){write(C+8,2);check((read(C+12)&6)==0&&read(C+16)==0,"CLEAR did not clear status");}
  void load(const Fixture& f){
    for(unsigned p=0;p<4096;p+=4)write(B+p,word(f.mem,p));
    write(C+24,f.shift);
  }
  void finish(){
    for(unsigned n=0;n<30000;++n){
      if(!(read(C+12)&1))return;
      for(int j=0;j<67;++j)tick();
    }
    throw std::runtime_error("Inference watchdog");
  }
  void compare(const Fixture& f){
    check((read(C+12)&3)==2,"DONE/BUSY mismatch");check(read(C+28)==f.prediction,"Prediction mismatch");
    auto expected=f.mem;std::copy(f.expected.begin(),f.expected.end(),expected.begin()+0x950);
    for(unsigned p=0;p<4096;p+=4)check(read(B+p)==word(expected,p),"Memory mismatch at "+std::to_string(p));
    ++full;
  }
  uint32_t infer(const Fixture& f){
    load(f);uint64_t r=reads,w=writes;write(C+8,1);finish();
    check(reads-r==120394&&writes-w==154,"Inference memory-operation counts");
    check(read(C+12)==2,"Unexpected inference error");
    uint32_t cycles=read(C+20);check(cycles>181903,"Synchronous SRAM latency not counted");
    compare(f);return cycles;
  }
};
static Sim *driver_sim;
#define CNN_MMIO_READ32(a) driver_sim->read(uint32_t(a))
#define CNN_MMIO_WRITE32(a,v) driver_sim->write(uint32_t(a),uint32_t(v))
#include "../sw/cnn_driver.h"
int main(int argc,char**argv){
  Verilated::commandArgs(argc,argv);
  try{
    check(argc==3,"usage: peripheral fixtures count");
    unsigned count=unsigned(std::stoul(argv[2]));check(count==55,"Expected 55 fixtures");
    std::vector<Fixture> fs;
    for(unsigned i=0;i<count;++i){char name[32];std::snprintf(name,sizeof(name),"/%03u.bin",i);fs.emplace_back(std::string(argv[1])+name);}
    Sim s;unsigned protocol_tests=0;uint32_t direct_cycles=0,max_cycles=0;
    for(int mode=0;mode<2;++mode){
      s.mode=mode;s.reset();
      check(s.read(C)==0x434e4e31&&s.read(C+4)==0x10000&&s.read(C+24)==9,"Reset/identity registers");
      check(s.read(C+12)==0&&s.read(C+16)==0&&s.read(C+20)==0,"Reset status");
      for(int id=0;id<8;++id)check(!s.transaction(C,false,0,15,id).err,"ID test");
      // All byte masks, including zero, around both bank edges and buffer endpoints.
      for(unsigned p:{0u,0x7fcu,0x800u,0xffcu})for(unsigned be=0;be<16;++be){
        s.write(B+p,0x12345678);s.write(B+p,0x89abcdef,be);uint32_t v=0x12345678;
        for(unsigned b=0;b<4;++b)if(be&(1u<<b))v=(v&~(255u<<(8*b)))|(0x89abcdefu&(255u<<(8*b)));
        check(s.read(B+p)==v,"Byte enable/bank boundary mismatch");++protocol_tests;
      }
      std::array<uint32_t,1024> shadow{};
      for(unsigned p=0;p<1024;++p)s.write(B+4*p,0);
      for(unsigned n=0;n<2048;++n){
        unsigned p=s.random()%1024,be=s.random()%16;uint32_t v=s.random();s.write(B+4*p,v,be);
        for(unsigned b=0;b<4;++b)if(be&(1u<<b))shadow[p]=(shadow[p]&~(255u<<(8*b)))|(v&(255u<<(8*b)));
        check(s.read(B+4*p)==shadow[p],"Random SRAM scoreboard mismatch");++protocol_tests;
      }
      for(unsigned p=0;p<1024;++p)check(s.read(B+4*p)==shadow[p],"SRAM aliasing");
      // Decode, alignment, register write permissions, reserved control bits and partial writes.
      for(uint32_t a:{C+32,C+4092,C-4096,B+4096,C+0x10000000,C+1,B+1}){s.bad(a,false,0);++protocol_tests;}
      for(unsigned off:{0u,4u,12u,16u,20u,28u}){s.bad(C+off,true,0);++protocol_tests;}
      for(unsigned be=0;be<15;++be){s.bad(C+8,true,1,be);s.bad(C+24,true,10,be);protocol_tests+=2;}
      s.bad(C+8,true,4);s.bad(C+24,true,31);s.bad(C+24,true,0x10000009);
      check(s.read(C+16)==1&&s.read(C+24)==9,"First-error latch / rejected shift changed config");
      s.clear();s.bad(C+24,true,31);check(s.read(C+16)==2,"Shift error code");s.clear();
      // Back-to-back held req across RESPONSE. B cannot replace the outstanding A.
      s.d.req_i=1;s.d.we_i=0;s.d.addr_i=C;s.d.be_i=15;s.d.aid_i=2;s.tick();check(s.last_grant,"Pipeline A grant");
      s.d.addr_i=C+4;s.d.aid_i=5;s.tick();check(s.last_response&&!s.last_grant&&s.last.data==0x434e4e31,"Pipeline A response");
      s.tick();check(s.last_grant,"Pipeline B grant");s.d.req_i=0;s.tick();check(s.last_response&&s.last.data==0x10000,"Pipeline B response");++protocol_tests;
      for(const auto& f:fs){uint32_t c=s.infer(f);if(mode==0){if(!direct_cycles)direct_cycles=c;check(c==direct_cycles,"Direct SRAM nondeterministic cycles");}max_cycles=std::max(c,max_cycles);}
      // Busy conflicts must not issue SRAM requests, change config, clear DONE/error or restart.
      const auto& f=fs[0];s.load(f);s.write(C+8,1);
      uint64_t r=s.reads,w=s.writes;
      s.bad(B,true,0);s.bad(B,false,0);s.bad(C+8,true,1);s.bad(C+8,true,2);s.bad(C+24,true,30);
      check(s.read(C+16)==3&&s.read(C+24)==f.shift,"Busy-conflict status");
      s.finish();check(s.reads-r<=120394&&s.writes-w==154,"Unexpected busy CPU SRAM access");s.compare(f);
      check(s.read(C+12)==6,"Busy error not sticky through DONE");s.clear();
      // Retain weights: load a new image only, then CLEAR+START in one write.
      const auto& g=fs[1];for(unsigned p=0;p<784;p+=4)s.write(B+p,word(g.mem,p));
      s.write(C+8,3);s.finish();s.compare(g);check(s.read(C+12)==2,"CLEAR+START");
      // Reset an active inference, then fully reload and restart.
      s.write(C+8,1);for(unsigned n=0;n<100;++n)s.tick();s.reset();
      check(s.read(C+12)==0&&s.read(C+20)==0,"Inference reset");s.infer(f);protocol_tests+=8;
    }
    s.mode=1;s.reset();const auto& f=fs[0];
    // Prolonged denied grant, with CSR polling still available while BUSY.
    s.load(f);s.force_grant=0;s.write(C+8,1);
    for(unsigned n=0;n<100;++n)check((s.read(C+12)&1)==1,"Lost BUSY on memory stall");
    s.force_grant=-1;s.finish();s.compare(f);++protocol_tests;
    // CPU read and write response errors: failed write must not modify SRAM.
    s.write(B,0x11223344);s.d.fault_i=1;s.bad(B,true,0xdeadbeef);s.d.fault_i=0;
    check(s.read(C+16)==4&&s.read(B)==0x11223344,"CPU memory error/failed write");s.clear();
    s.d.fault_i=1;s.bad(B,false,0);s.d.fault_i=0;check(s.read(C+16)==4,"CPU read error");s.clear();
    // Engine memory failure: read then write. Inject only into the selected local request.
    for(int kind=0;kind<2;++kind){
      s.load(f);s.write(C+8,1);bool injected=false;
      for(unsigned n=0;n<1000000&&!injected;++n){
        s.d.fault_i=s.d.local_req_o&&(int(s.d.local_we_o)==kind);
        s.tick();injected=s.local_fire&&s.d.fault_i;
      }
      check(injected,"Fault target missing");s.d.fault_i=0;s.finish();
      check(s.read(C+12)==4&&s.read(C+16)==4,"Engine memory abort missing");s.clear();++protocol_tests;
    }
    // Int32 overflow propagates through wrapper, plus first-error retention.
    for(int prior=0;prior<2;++prior){
      s.load(f);s.write(B+0x374,0x7fffffff);s.write(B,0xffffffff);s.write(B+0x310,0x7f7f7f7f);
      s.write(C+8,1);if(prior)s.bad(C+32,false,0);s.finish();
      check(s.read(C+12)==4&&s.read(C+16)==(prior?1u:5u),"Overflow / first-error priority");s.clear();++protocol_tests;
    }
    // Cancel OBI before SRAM grant and after SRAM commit but before response delivery.
    for(int committed=0;committed<2;++committed){
      s.write(B,0x55667788);s.force_grant=committed?1:0;s.force_response=0;
      s.d.req_i=1;s.d.addr_i=B;s.d.we_i=1;s.d.wdata_i=0xaabbccdd;s.d.be_i=15;s.d.aid_i=6;
      s.tick();check(s.last_grant,"Reset test grant");s.d.req_i=0;
      for(int n=0;n<10;++n)s.tick();s.reset();
      check(s.read(B)==(committed?0xaabbccddu:0x55667788u),"Reset transaction commit boundary");++protocol_tests;
    }
    // Reset while an engine output write is awaiting response; reload before reuse.
    s.load(f);s.write(C+8,1);bool output=false;
    for(unsigned n=0;n<1000000&&!output;++n){s.tick();output=s.local_fire&&s.d.local_we_o;}
    check(output,"Output reset target missing");s.reset();s.infer(f);++protocol_tests;
    // Execute the actual C driver against this RTL through MMIO callbacks.
    driver_sim=&s;s.mode=0;s.reset();s.load(f);
    check(cnn_load_model(f.mem.data()+0x310,f.shift)==CNN_OK,"Driver model load");
    check(cnn_load_image(f.mem.data())==CNN_OK&&cnn_start()==CNN_OK,"Driver image/start");
    check(cnn_load_image(f.mem.data())==CNN_BUSY&&cnn_clear()==CNN_BUSY,"Driver busy guard");
    unsigned prediction=99;uint32_t cycles=0;
    check(cnn_wait(1000000,&prediction,&cycles)==CNN_OK&&prediction==f.prediction&&cycles==direct_cycles,"Driver wait");
    s.compare(f);check(cnn_load_model(f.mem.data(),31)==CNN_ARGUMENT,"Driver argument guard");
    s.mode=1;s.reset();s.load(f);s.force_grant=0;check(cnn_start()==0,"Timeout start");
    check(cnn_wait(16,nullptr,nullptr)==CNN_TIMEOUT&&(cnn_status()&1),"Driver timeout must not cancel hardware");
    s.force_grant=-1;s.finish();s.compare(f);s.clear();s.bad(C+32,false,0);
    check(cnn_wait(1,nullptr,nullptr)==CNN_ERROR&&cnn_error_code()==1,"Driver error status");
    check(cnn_clear()==CNN_OK,"Driver clear");
    check(!s.pending,"Final outstanding transaction");
    std::cout<<"{\"status\":\"CP2B_SIM_PASS\",\"fixtures\":"<<count
      <<",\"full_inferences_checked\":"<<s.full<<",\"protocol_checks\":"<<protocol_tests
      <<",\"direct_sram_cycles\":"<<direct_cycles<<",\"max_random_stall_cycles\":"<<max_cycles
      <<",\"obi_requests\":"<<s.accepts<<",\"obi_responses\":"<<s.responses
      <<",\"reset_cancelled_requests\":"<<(s.accepts-s.responses)<<",\"driver_checks\":10}\n";
  }catch(const std::exception&e){std::cerr<<"FAIL: "<<e.what()<<"\n";return 1;}
}
