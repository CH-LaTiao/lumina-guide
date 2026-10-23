"""Read the latest accel/gyro snapshot published by core/frame_server.cpp."""
import math
import mmap
import os
import struct
import time

MAGIC=0x554D494C
VERSION=1
SIZE=128
SNAPSHOT=struct.Struct('<IIQQQQQ6f2fI11I')
SEQ=struct.Struct('<Q')
SEQ_OFFSET=16


class ImuShm:
    def __init__(self,name='/lumina-frames-v1-imu',path=None):
        self.path=path or f'/dev/shm/{name.lstrip("/")}'
        self.fd=os.open(self.path,os.O_RDONLY)
        try:
            if os.fstat(self.fd).st_size<SIZE:raise ValueError('Incomplete IMU snapshot')
            if hasattr(mmap,'PROT_READ'):
                self.mm=mmap.mmap(self.fd,SIZE,prot=mmap.PROT_READ)
            else:
                self.mm=mmap.mmap(self.fd,SIZE,access=mmap.ACCESS_READ)
            magic,version,epoch=self._head()
            if magic!=MAGIC or version!=VERSION or not epoch:
                raise ValueError('Invalid or incompatible IMU snapshot')
            self.epoch=epoch
        except Exception:
            if hasattr(self,'mm'):self.mm.close()
            os.close(self.fd);self.fd=None
            raise

    def _head(self):
        return struct.unpack_from('<IIQ',self.mm,0)

    def close(self):
        self.mm.close();os.close(self.fd)

    def latest(self,retries=4):
        for _ in range(retries):
            before=SEQ.unpack_from(self.mm,SEQ_OFFSET)[0]
            if before%2:continue
            values=SNAPSHOT.unpack_from(self.mm,0)
            after=SEQ.unpack_from(self.mm,SEQ_OFFSET)[0]
            if before!=after:continue
            (magic,version,epoch,seq,accel_ts,gyro_ts,published_ns,
             ax,ay,az,gx,gy,gz,accel_temp,gyro_temp,flags,*_) = values
            if magic!=MAGIC or version!=VERSION or epoch!=self.epoch or not seq:return None
            if not all(math.isfinite(v) for v in (ax,ay,az,gx,gy,gz,accel_temp,gyro_temp)):
                return None
            age_ms=(time.monotonic_ns()-published_ns)/1e6
            return {'epoch':epoch,'seq':seq,'accel_ts_us':accel_ts,'gyro_ts_us':gyro_ts,
                'published_ns':published_ns,'age_ms':round(age_ms,1),
                'accel_valid':bool(flags&1),'gyro_valid':bool(flags&2),
                'accel':{'x':ax,'y':ay,'z':az,'temperature_c':accel_temp},
                'gyro':{'x':gx,'y':gy,'z':gz,'temperature_c':gyro_temp}}
        return None
