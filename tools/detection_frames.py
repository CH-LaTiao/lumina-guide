"""Small in-memory cache for exact-frame HTTP retrieval. Caller serializes access."""
import math
import time
from collections import OrderedDict

class DetectionFrames:
    def __init__(self, clock=time.monotonic, ttl=2, max_frames=24, max_bytes=8*1024*1024):
        self.clock=clock;self.ttl=ttl;self.max_frames=max_frames;self.max_bytes=max_bytes
        self.items=OrderedDict();self.byte_count=0

    def _remove(self,key):
        self.byte_count-=len(self.items.pop(key)[1])

    def _prune(self,now):
        for key,(captured,_) in list(self.items.items()):
            if not 0<=now-captured<=self.ttl:self._remove(key)

    def clear(self):
        self.items.clear();self.byte_count=0

    def put(self,frame_id,captured,jpeg):
        now=self.clock();self._prune(now)
        if not math.isfinite(captured) or not 0<=now-captured<=self.ttl or len(jpeg)>self.max_bytes:return
        if frame_id in self.items:self._remove(frame_id)
        self.items[frame_id]=(captured,jpeg);self.byte_count+=len(jpeg)
        while len(self.items)>self.max_frames or self.byte_count>self.max_bytes:
            self._remove(next(iter(self.items)))

    def get(self,frame_id):
        now=self.clock();self._prune(now)
        item=self.items.get(frame_id)
        return (item[1],(now-item[0])*1000) if item else (None,None)
