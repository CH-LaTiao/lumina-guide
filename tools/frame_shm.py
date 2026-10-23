"""V2 ring: kernel-locked copies, producer epoch and host monotonic timestamps."""
import fcntl
import math
import mmap
import os
import struct

MAGIC=0x314E4D4C
VERSION=2
HEADER=struct.Struct('<6I4f2I2Q')
SLOT=struct.Struct('<6Q4If7I')
SEQ=struct.Struct('<Q')
HEADER_SIZE=64
SLOT_HEADER=96

class FrameShm:
    def __init__(self,name='/lumina-frames-v1',path=None):
        self.path=path or f'/dev/shm/{name.lstrip("/")}'
        self.fd=os.open(self.path,os.O_RDONLY);self.mm=None
        try:
            fcntl.flock(self.fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
            size=os.fstat(self.fd).st_size
            if size<HEADER_SIZE:raise ValueError('Incomplete ring header')
            self.mm=mmap.mmap(self.fd,size,prot=mmap.PROT_READ)
            v=HEADER.unpack_from(self.mm,0)
            magic,version,slots,slot_size,dc,cc=v[:6]
            if (magic!=MAGIC or version!=VERSION or not v[13] or not v[12]
                or not 1<=slots<=8 or not 0<dc<=4*1024*1024 or not 0<cc<=4*1024*1024
                or slot_size!=SLOT_HEADER+dc+cc or size!=HEADER_SIZE+slots*slot_size
                or not all(math.isfinite(x) for x in v[6:10]) or v[6]<=0 or v[7]<=0
                or not 0<v[10]<=1920 or not 0<v[11]<=1080):
                raise ValueError('Invalid or incompatible v2 frame ring')
            self.slots=slots;self.slot_size=slot_size
            self.depth_capacity=dc;self.color_capacity=cc;self.epoch=v[12]
            self.intrinsics=dict(zip(('fx','fy','cx','cy','width','height'),v[6:12]))
        except Exception:
            if self.mm is not None:self.mm.close()
            os.close(self.fd);self.fd=None
            raise
        finally:
            if self.fd is not None:fcntl.flock(self.fd,fcntl.LOCK_UN)

    def close(self):
        self.mm.close();os.close(self.fd)

    def latest(self,retries=4):
        try:fcntl.flock(self.fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:return None
        try:
            v=HEADER.unpack_from(self.mm,0)
            if v[12]!=self.epoch or not v[13]:raise ValueError('Producer epoch changed')
            seq,offset=max((SEQ.unpack_from(self.mm,HEADER_SIZE+i*self.slot_size)[0],
                HEADER_SIZE+i*self.slot_size) for i in range(self.slots))
            if not seq:return None
            f=SLOT.unpack_from(self.mm,offset)
            width,height,ds,cs,scale=f[6:11]
            if (not 0<ds<=self.depth_capacity or not 0<cs<=self.color_capacity
                or width!=self.intrinsics['width'] or height!=self.intrinsics['height']
                or ds!=width*height*2 or not math.isfinite(scale) or scale<=0
                or not 0<f[4]<=f[5]):return None
            payload=offset+SLOT_HEADER
            depth=bytes(self.mm[payload:payload+ds])
            color=bytes(self.mm[payload+self.depth_capacity:payload+self.depth_capacity+cs])
            return {'seq':seq,'frame_id':f[1],'depth_ts_us':f[2],'color_ts_us':f[3],
                'received_ns':f[4],'published_ns':f[5],'epoch':self.epoch,
                'width':width,'height':height,'depth_scale':scale},depth,color
        finally:fcntl.flock(self.fd,fcntl.LOCK_UN)
