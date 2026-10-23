#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>
// Diagnostic camera-image regions only. No ground model, body calibration or motion permission.
// level: 0 unknown, 1 no near evidence, 2 near evidence, 3 very-near evidence.
extern "C" int lumina_depth_regions(const std::uint16_t* data,int width,int height,
 double scale_mm,double age_ms,double* distances,double* coverage,int* levels) {
    if(!data||!distances||!coverage||!levels||width<6||height<4||width>4096||height>4096
       ||!std::isfinite(scale_mm)||scale_mm<=0||!std::isfinite(age_ms)||age_ms<0) return -1;
    for(int region=0;region<3;++region){
        distances[region]=-1;coverage[region]=0;levels[region]=0;
        if(age_ms>200) continue;
        std::vector<double> valid;int count=0,near=0,urgent=0,below_min=0;
        for(int y=height/4;y<3*height/4;++y) for(int x=region*width/3;x<(region+1)*width/3;++x){
            ++count;auto raw=data[y*width+x];if(raw==0)continue;
            double m=raw*scale_mm/1000.0;
            if(m<0.3){++below_min;continue;} // Outside configured range: unknown, never clearance.
            valid.push_back(m);if(m<1.5)++near;if(m<0.8)++urgent;
        }
        coverage[region]=count?static_cast<double>(valid.size())/count:0;
        if(valid.empty())continue;
        auto nth=valid.begin()+static_cast<long>(0.1*(valid.size()-1));std::nth_element(valid.begin(),nth,valid.end());
        distances[region]=*nth;
        // Require nontrivial support to avoid a few isolated near-depth outliers.
        int support=std::max(6,static_cast<int>(std::ceil(count*0.01)));
        if(urgent>=support)levels[region]=3;
        else if(near>=support)levels[region]=2;
        else if(coverage[region]>=0.6 && below_min==0)levels[region]=1;
    }
    return 0;
}
