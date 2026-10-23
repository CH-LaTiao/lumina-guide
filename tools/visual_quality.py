"""Centre range readout and RGB usability checks for the diagnostic preview.

The 336L has no separate LiDAR component; its ranging is the IR laser speckle
plus stereo depth engine. These helpers only read that depth stream.
"""
import numpy as np

def center_range(depth,scale_mm,window=15):
    h,w=depth.shape;cy,cx=h//2,w//2;half=window//2
    patch=depth[max(0,cy-half):cy+half+1,max(0,cx-half):cx+half+1]
    valid=patch[patch>0]
    if valid.size<9:return None
    return float(np.median(valid)*scale_mm/1000.0)

def rgb_quality(rgb):
    if rgb is None:return {'state':'UNAVAILABLE','mean':None,'over_ratio':None,'under_ratio':None}
    luminance=rgb.astype(np.float32).mean(axis=2)
    mean=float(luminance.mean());over=float((luminance>=250).mean());under=float((luminance<=20).mean())
    if over>=0.35:state='OVEREXPOSED'
    elif mean<25:state='LOW_LIGHT'
    elif under>=0.6:state='MOSTLY_DARK'
    else:state='OK'
    return {'state':state,'mean':round(mean,1),'over_ratio':round(over,3),'under_ratio':round(under,3)}
