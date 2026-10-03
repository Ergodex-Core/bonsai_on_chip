module four_state_tb;
logic[15:0]cout;logic[2:0]eew;wire[15:0]reference_value,patched_value;
leaf dut(.*);
integer count=0;
function automatic logic symbol(input integer v);
case(v)0:symbol=0;1:symbol=1;2:symbol=1'bx;default:symbol=1'bz;endcase
endfunction
initial begin
for(integer e=0;e<64;e++)begin
 for(integer k=0;k<3;k++)eew[k]=symbol((e>>(2*k))&3);
 for(integer base=0;base<4;base++)begin
  for(integer bitpos=0;bitpos<16;bitpos++)begin
   for(integer value=0;value<4;value++)begin
    case(base)0:cout=16'h0000;1:cout=16'hffff;2:cout=16'haaaa;default:cout=16'h5555;endcase
    cout[bitpos]=symbol(value);#1;
    if(reference_value!==patched_value)$fatal(1,"FAIL eew=%b cout=%b ref=%b patched=%b",eew,cout,reference_value,patched_value);
    count=count+1;
   end
  end
 end
end
$display("PASS %0d four-state directed cases, %0d output bits",count,count*16);$finish;
end
endmodule
