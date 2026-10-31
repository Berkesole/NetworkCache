#include "probe_control.hpp"
#include <cassert>
int main(){
 using namespace experiment_probe;
 assert(identity(2,"900000001","federation_v4h3_probe_B"));
 assert(!identity(3,"900000001","federation_v4h3_probe_B"));
 assert(!identity(2,"42","federation_v4h3_probe_B"));
 retirement(true,true,1,100,30100,31000,30000);
 for(int i=0;i<6;i++) { bool threw=false;try{
 retirement(i!=0,i!=1,i==2?2:1,100,i==3?30099:30100,i==4?30000:31000,i==5?40000:30000);
 }catch(const std::runtime_error&){threw=true;}assert(threw); }
}
