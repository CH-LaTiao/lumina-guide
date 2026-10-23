import os,struct,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from imu_shm import ImuShm,MAGIC,VERSION,SNAPSHOT


class ImuShmTests(unittest.TestCase):
    def test_reads_consistent_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'imu'
            path.write_bytes(b'\0'*128)
            data=SNAPSHOT.pack(MAGIC,VERSION,123,2,10,11,time.monotonic_ns(),
                0.1,0.2,9.8,0.01,0.02,0.03,31.,32.,3,*([0]*11))
            path.write_bytes(data)
            reader=ImuShm(path=str(path))
            try:
                value=reader.latest()
                self.assertEqual((value['seq'],value['accel_valid'],value['gyro_valid']),(2,True,True))
                self.assertAlmostEqual(value['accel']['z'],9.8,delta=1e-5)
                self.assertAlmostEqual(value['gyro']['y'],.02)
            finally:reader.close()

    def test_rejects_wrong_epoch(self):
        if os.name!='posix':self.skipTest('Windows locks an active mmap fixture')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'imu';path.write_bytes(b'\0'*128)
            data=SNAPSHOT.pack(MAGIC,VERSION,456,2,0,0,time.monotonic_ns(),
                0,0,9.8,0,0,0,0,0,1,*([0]*11))
            path.write_bytes(data)
            reader=ImuShm(path=str(path))
            try:
                changed=SNAPSHOT.pack(MAGIC,VERSION,999,2,0,0,time.monotonic_ns(),
                    0,0,9.8,0,0,0,0,0,1,*([0]*11))
                path.write_bytes(changed)
                self.assertIsNone(reader.latest())
            finally:reader.close()


if __name__=='__main__':unittest.main()
