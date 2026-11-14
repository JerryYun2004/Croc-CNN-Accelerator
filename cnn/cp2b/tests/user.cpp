#include "Vuser_tb.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <array>
#include <stdexcept>
#include <cstdio>
#include <cstdint>
static Vuser_tb d;
static unsigned next_id=0;
static uint64_t transactions=0;
static void check(bool x,const char*s){if(!x)throw std::runtime_error(s);}
static void tick(){d.clk_i=0;d.eval();check(!d.side_effect_o,"Unexpected manager/IRQ/optional output");d.clk_i=1;d.eval();d.clk_i=0;d.eval();}
static uint32_t tx(uint32_t addr,bool write=false,uint32_t value=0,bool error=false,unsigned be=15){
  unsigned id=next_id++%8;d.req_i=1;d.addr_i=addr;d.we_i=write;d.wdata_i=value;d.be_i=be;d.aid_i=id;d.eval();
  unsigned n=0;while(!d.gnt_o){tick();check(++n<100,"User domain grant timeout");}
  tick();d.req_i=0;d.addr_i=0xdeadc0de;d.aid_i=7-id;d.eval();
  n=0;while(!d.rvalid_o){tick();check(++n<100,"User domain response timeout");}
  check(d.rid_o==id,"User RID mismatch");check(bool(d.err_o)==error,"User OBI error mismatch");
  uint32_t result=d.rdata_o;tick();++transactions;return result;
}
#define CNN_MMIO_READ32(a) tx(uint32_t(a))
#define CNN_MMIO_WRITE32(a,v) tx(uint32_t(a),true,uint32_t(v))
#include "../sw/cnn_driver.h"
int main(int argc,char**argv){
  Verilated::commandArgs(argc,argv);
  try{
    check(argc==3,"usage: user fixtures count");unsigned count=std::stoul(argv[2]);check(count==55,"Fixture count");
    d.clk_i=0;d.rst_ni=0;d.req_i=0;d.eval();tick();d.rst_ni=1;tick();
    check(tx(0x20001000)==0x434e4e31,"ID through Croc demux");
    // Switch between default error subordinate and CNN subordinate with no idle gap.
    for(unsigned n=0;n<8;++n){check(tx(0x40000000,false,0,true)==0xbadcab1e,"Default error subordinate");check(tx(0x20001000)==0x434e4e31,"Return route");}
    tx(0x20000000,false,0,true);tx(0x20003000,false,0,true);cnn_clear();
    for(unsigned i=0;i<count;++i){
      char suffix[32];std::snprintf(suffix,sizeof(suffix),"/%03u.bin",i);
      std::ifstream f(std::string(argv[1])+suffix,std::ios::binary);check(bool(f),"Missing fixture");
      std::array<uint8_t,4096> mem;std::array<uint8_t,184> expected;
      f.read(reinterpret_cast<char*>(mem.data()),4096);f.read(reinterpret_cast<char*>(expected.data()),184);
      unsigned pred=unsigned(f.get()),shift=unsigned(f.get());check(bool(f),"Fixture read");
      check(cnn_load_model(mem.data()+0x310,shift)==0&&cnn_load_image(mem.data())==0&&cnn_start()==0,"Driver through user domain");
      unsigned actual=99;uint32_t cycles=0;
      check(cnn_wait(1000000,&actual,&cycles)==0&&actual==pred&&cycles==302451,"User domain inference");
      for(unsigned p=0;p<184;p+=4){
        uint32_t v=0;for(unsigned b=0;b<4;++b)v|=uint32_t(expected[p+b])<<(b*8);
        check(tx(0x20002950+p)==v,"User domain pooled/logit output");
      }
      check(cnn_clear()==0,"User domain clear");
    }
    std::cout<<"{\"status\":\"USER_DOMAIN_PASS\",\"inferences\":"<<count<<",\"obi_transactions\":"<<transactions<<",\"cve2_cpu_executed\":false}\n";
  }catch(const std::exception&e){std::cerr<<"FAIL: "<<e.what()<<"\n";return 1;}
}
