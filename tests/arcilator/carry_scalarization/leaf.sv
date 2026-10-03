module leaf(input logic[3:0][3:0] cout,input logic[2:0] eew,output logic[3:0][3:0] reference_value,patched_value);
localparam logic[2:0] EEW32=4,EEW16=3;
function automatic logic[3:0][3:0] f_cout2cin(input logic[3:0][3:0] c,input logic[2:0] width_sel);
 for(int i=0;i<4;i++)begin
  f_cout2cin[i][0]=0;
  case(width_sel)
   EEW32:for(int j=1;j<4;j++)f_cout2cin[i][j]=j%4==0?0:c[i][j-1];
   EEW16:for(int j=1;j<4;j++)f_cout2cin[i][j]=j%2==0?0:c[i][j-1];
   default:for(int j=1;j<4;j++)f_cout2cin[i][j]=0;
  endcase
 end
endfunction
assign reference_value=f_cout2cin(cout,eew);
for(genvar g=0;g<4;g++)begin
 for(genvar b=0;b<4;b++)begin
  if(b==0)assign patched_value[g][b]=0;
  else assign patched_value[g][b]=(((eew===EEW32)&&(b%4!=0))||((eew===EEW16)&&(b%2!=0)))?cout[g][b-1]:1'b0;
 end
end
endmodule
