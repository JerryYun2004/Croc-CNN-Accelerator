#include "Vprimitives.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <algorithm>
int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv); Vprimitives d;
  uint64_t tests=0;
  try {
    for (int a=-128; a<128; ++a) for (int b=-128; b<128; ++b)
      for (int64_t acc : {int64_t(0), int64_t(INT32_MAX), int64_t(INT32_MIN)}) {
        d.a_i=uint8_t(a); d.b_i=uint8_t(b); d.acc_i=uint32_t(acc); d.shift_i=9; d.eval();
        int64_t sum=acc+a*b;
        if (d.sum_o!=uint32_t(sum) || bool(d.overflow_o)!=(sum>INT32_MAX || sum<INT32_MIN))
          throw std::runtime_error("MAC mismatch");
        ++tests;
      }
    uint64_t quant_tests=0;
    for (unsigned s=0;s<=30;++s) {
      auto check=[&](int64_t v) {
        if(v<INT32_MIN || v>INT32_MAX) return;
        d.acc_i=uint32_t(v); d.shift_i=s; d.eval();
        int64_t q = v<=0 ? 0 : ((v+(s?(int64_t(1)<<(s-1)):0))>>s);
        q=std::min<int64_t>(127,q);
        if(d.value_o!=q) throw std::runtime_error("Requant/ReLU mismatch");
        ++quant_tests;
      };
      for(int v=-256;v<=256;++v) check(v);
      for(int64_t q : {int64_t(1),int64_t(2),int64_t(126),int64_t(127),int64_t(128)})
        for(int off=-2;off<=2;++off) {
          int64_t v=(q<<s)-(s?(int64_t(1)<<(s-1)):0)+off;
          check(v);check(-v);
        }
      check(INT32_MIN);check(INT32_MAX);
    }
    std::cout << "{\"status\":\"PRIMITIVES_PASS\",\"mac_cases\":" << tests
              << ",\"quant_cases\":" << quant_tests << "}\n";
  } catch(const std::exception& e) { std::cerr<<e.what()<<"\n";return 1; }
  d.final();return 0;
}
