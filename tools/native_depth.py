import ctypes
from pathlib import Path
import numpy as np

class NativeDepth:
    def __init__(self):
        root=Path(__file__).resolve().parents[1]
        path=root/'build/liblumina-depth.so'
        if not path.exists():path=root/'bin/liblumina-depth.so'
        self.lib=ctypes.CDLL(str(path))
        self.fn=self.lib.lumina_depth_regions
        self.fn.argtypes=[ctypes.POINTER(ctypes.c_uint16),ctypes.c_int,ctypes.c_int,ctypes.c_double,ctypes.c_double,
                          ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_int)]
        self.fn.restype=ctypes.c_int
    def evaluate(self,raw,scale,age_ms):
        data=np.ascontiguousarray(raw,dtype=np.uint16)
        if data.ndim!=2:raise ValueError('Depth must be 2D')
        distances=(ctypes.c_double*3)();coverage=(ctypes.c_double*3)();levels=(ctypes.c_int*3)()
        result=self.fn(data.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),data.shape[1],data.shape[0],scale,age_ms,distances,coverage,levels)
        if result:raise ValueError('Invalid native depth input')
        return [{'region':i,'level':levels[i],'distance_p10_m':distances[i] if distances[i]>=0 else None,'coverage':coverage[i]} for i in range(3)]
