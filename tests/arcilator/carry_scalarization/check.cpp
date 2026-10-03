#include "Vleaf.h"
#include <cstdio>
int main(){Vleaf dut;unsigned cases=0;for(unsigned e=0;e<8;e++)for(unsigned c=0;c<65536;c++){dut.eew=e;dut.cout=c;dut.eval();const unsigned gold=e==4 ? ((c<<1)&0xeeeeu) : e==3 ? ((c<<1)&0xaaaau) : 0u;if(dut.reference_value!=dut.patched_value || dut.patched_value!=gold){std::printf("FAIL e=%u c=%u\n",e,c);return 1;}cases++;}std::printf("PASS %u exhaustive two-state cases, %u output bits\n",cases,cases*16);return 0;}
