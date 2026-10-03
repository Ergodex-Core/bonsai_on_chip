#include "Vcoral_weight_axi.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <stdexcept>
#include <vector>
#include <fstream>
static void check(bool b,const char*m){if(!b)throw std::runtime_error(m);}
struct Sim {
 Vcoral_weight_axi d; std::vector<uint8_t> rom; std::mt19937 rng{7193};
 bool inject_error=false; bool pending=false; unsigned pending_addr=0,delay=0; uint64_t ticks=0;
 Sim():rom(4096){d.reset=1;for(int i=0;i<4;i++)step();d.reset=0;}
 void step(){
  d.clk=0; d.storage_ready=!pending&&(rng()%4!=0);
  d.storage_rsp_valid=pending&&delay==0;d.storage_rsp_error=inject_error&&pending;
  for(unsigned i=0;i<4;i++){uint32_t v=0;for(unsigned j=0;j<4;j++)v|=uint32_t(pending?rom.at((pending_addr+4*i+j)%rom.size()):0)<<(8*j);d.storage_rsp_data[i]=v;}d.eval();
  bool accepted=d.storage_valid&&d.storage_ready;
  bool consumed=d.storage_rsp_valid&&d.storage_rsp_ready;
  unsigned addr=d.storage_addr;
  d.clk=1;d.eval();ticks++;
  if(consumed)pending=false;
  if(pending&&delay)delay--;
  if(accepted){check(!pending,"multiple outstanding storage requests");pending=true;pending_addr=addr;delay=rng()%4;}
 }
 unsigned write(unsigned off,uint32_t word,bool data_first=false){
  d.s_awaddr=off;d.s_awid=17;d.s_awsize=2;d.s_awlen=0;d.s_wlast=1;
  for(int i=0;i<4;i++)d.s_wdata[i]=0;
  d.s_wdata[(off%16)/4]=word;d.s_wstrb=15u<<(off%16);
  bool a=false,w=false;
  for(unsigned n=0;!a||!w;n++){
   check(n<10000,"write acceptance timeout");
   d.s_awvalid=!a&&(!data_first||n>=3);d.s_wvalid=!w&&(data_first||n>=3);
   d.clk=0;d.eval();bool af=d.s_awvalid&&d.s_awready,wf=d.s_wvalid&&d.s_wready;
   step();a|=af;w|=wf;
  }
  d.s_awvalid=d.s_wvalid=0;
  for(int n=0;!d.s_bvalid;n++){check(n<10000,"B timeout");step();}
  unsigned resp=d.s_bresp;check(d.s_bid==17,"write ID");
  for(int n=0;n<3;n++){step();check(d.s_bvalid&&d.s_bresp==resp,"B stability");}
  d.s_bready=1;step();d.s_bready=0;return resp;
 }
 uint32_t read(unsigned addr,unsigned expected_resp=0){
  d.s_araddr=addr;d.s_arid=23;d.s_arsize=2;d.s_arlen=0;d.s_arvalid=1;
  for(int n=0;;n++){
   check(n<10000,"AR timeout");d.clk=0;d.eval();bool fire=d.s_arready;step();if(fire)break;
  }
  d.s_arvalid=0;
  for(int n=0;!d.s_rvalid;n++){check(n<10000,"R timeout");step();}
  check(d.s_rresp==expected_resp,"read response");check(d.s_rid==23&&d.s_rlast,"read ID/last");
  uint32_t word=d.s_rdata[(addr%16)/4];std::array<uint32_t,4> held;
  for(int i=0;i<4;i++)held[i]=d.s_rdata[i];
  for(int n=0;n<3;n++){step();check(d.s_rvalid,"R valid stability");for(int i=0;i<4;i++)check(held[i]==d.s_rdata[i],"R data stability");}
  d.s_rready=1;step();d.s_rready=0;return word;
 }
};
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);
 try {
  Sim s; unsigned arithmetic_checks=0,byte_checks=0;
  constexpr unsigned M=0x60000000,R=0x40000000;
  std::ifstream model; if(argc>1) {model.open(argv[1],std::ios::binary);check(bool(model),"open actual checkpoint");}
  const int trials=argc>1?100:50;
  for(int trial=0;trial<trials;trial++){
   for(auto &b:s.rom)b=s.rng()%256;
   if(trial>=50){model.seekg(5953472+1088*(trial-50));model.read((char*)s.rom.data(),1088);check(bool(model),"read original PQ2 bytes");}
   // Original packed native bytes including +2, FP16 bit patterns unmodified.
   std::array<int8_t,128>a;
   for(int i=0;i<128;i++)a[i]=(int8_t)(s.rng()%256);
   for(unsigned i=0;i<32;i++){
    check(s.write(M+0x100+4*i,34*i,trial%2)==0,"weight address write");
   }
   for(unsigned i=0;i<128;i+=4){
    uint32_t x=0;for(unsigned j=0;j<4;j++)x|=uint32_t(uint8_t(a[i+j]))<<(8*j);
    check(s.write(M+0x200+i,x,trial%2)==0,"activation write");
   }
   check(s.write(M+0x280,0x3c004000)==0,"activation scales");
   check(s.read(M+0x280)==0x3c004000,"scale readback");
   for(unsigned i=0;i<64;i+=4){
    uint32_t exp=0;for(unsigned j=0;j<4;j++)exp|=uint32_t(s.rom[i+j])<<(8*j);
    check(s.read(R+i)==exp,"CPU ROM data");byte_checks+=4;
   }
   check(s.write(M+4,1)==0,"start");
   check(s.write(M+0x200,0)==2,"busy mutation rejection");
   unsigned status=0;for(int n=0;n<10000;n++){status=s.read(M);if(status&2)break;}
   check(status==2,"completion state");
   for(unsigned u=0;u<32;u++)for(unsigned g=0;g<4;g++){
    int exp=0;for(unsigned j=0;j<32;j++){unsigned lane=g*32+j;int code=(s.rom[34*u+2+lane/4]>>(2*(lane%4)))&3;exp+=(code-1)*int(a[lane]);}
    check(int32_t(s.read(M+0x400+u*16+g*4))==exp,"native subgroup result");arithmetic_checks++;
   }
   for(unsigned u=0;u<32;u+=2){uint32_t exp=0;for(unsigned j=0;j<2;j++)exp|=(uint32_t(s.rom[34*(u+j)])|(uint32_t(s.rom[34*(u+j)+1])<<8))<<(16*j);
    check(s.read(M+0x600+2*u)==exp,"untouched weight scales");}
  }
  s.inject_error=true;s.read(R,2);s.inject_error=false;
  s.inject_error=true;check(s.write(M+4,1)==0,"fault dispatch start");for(unsigned n=0;(s.read(M)&1)&&n<100;n++){}
  check((s.read(M)&6)==4,"backend engine fault propagation");s.inject_error=false;
  check(s.write(R,0)==3,"ROM write protection");check(s.read(R+4096,3)==0,"capacity boundary");
  check(s.write(M+8,0)==2,"invalid unit count");
  check(s.write(M+0x100,4096-33)==0,"set bad address");check(s.write(M+4,1)==2,"reject truncated group");
  check((s.read(M)&4)!=0,"bad address status");
  printf("{\"trials\":%d,\"actual_checkpoint_trials\":%d,\"integer_subgroup_checks\":%u,\"cpu_rom_byte_checks\":%u,\"clock_edges\":%llu,\"status\":\"PASS\"}\n",trials,trials-50,arithmetic_checks,byte_checks,(unsigned long long)s.ticks);
 }catch(const std::exception&e){fprintf(stderr,"FAIL: %s\n",e.what());return 1;}
}
