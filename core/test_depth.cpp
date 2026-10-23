#include <cstdint>
#include <vector>
extern "C" int lumina_depth_regions(const std::uint16_t*,int,int,double,double,double*,double*,int*);
int main(){
    constexpr int w=60,h=40;std::vector<std::uint16_t> d(w*h,0);double dist[3],cov[3];int level[3];
    auto eval=[&](double age=0){return lumina_depth_regions(d.data(),w,h,1,age,dist,cov,level);};
    if(eval()||level[0]!=0)return 1;
    std::fill(d.begin(),d.end(),2000);eval();if(level[0]!=1)return 2;
    d[12*w+5]=500;eval();if(level[0]!=1)return 3;
    for(int y=12;y<17;y++)for(int x=3;x<8;x++)d[y*w+x]=500;
    eval();if(level[0]!=3||level[1]!=1)return 4;
    eval(201);if(level[0]!=0||dist[0]!=-1)return 5;
    std::fill(d.begin(),d.end(),100);eval();if(level[0]!=0)return 6;
    return 0;
}
