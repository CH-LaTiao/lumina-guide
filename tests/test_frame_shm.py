import fcntl,mmap,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from frame_shm import HEADER,MAGIC,SLOT,VERSION,FrameShm,SLOT_HEADER

DC=8;CC=16;SIZE=SLOT_HEADER+DC+CC
class FrameShmTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ring'
        self.fd=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
        os.ftruncate(self.fd,64+3*SIZE);self.mm=mmap.mmap(self.fd,64+3*SIZE)
        HEADER.pack_into(self.mm,0,MAGIC,VERSION,3,SIZE,DC,CC,400.,400.,1.,1.,2,2,123,1)
    def tearDown(self):self.mm.close();os.close(self.fd);self.tmp.cleanup()
    def reader(self):
        r=FrameShm(path=self.path);self.addCleanup(r.close);return r
    def put(self,slot,seq,depth=b'A'*8,color=b'Z'*4):
        offset=64+slot*SIZE
        SLOT.pack_into(self.mm,offset,seq,seq,10,20,100,200,2,2,len(depth),len(color),1.,*([0]*7))
        self.mm[offset+SLOT_HEADER:offset+SLOT_HEADER+len(depth)]=depth
        self.mm[offset+SLOT_HEADER+DC:offset+SLOT_HEADER+DC+len(color)]=color
    def test_empty(self):self.assertIsNone(self.reader().latest())
    def test_latest(self):
        self.put(0,1);self.put(2,8,b'B'*8)
        meta,depth,color=self.reader().latest()
        self.assertEqual(meta['seq'],8);self.assertEqual(depth,b'B'*8);self.assertEqual(meta['epoch'],123)
        self.assertEqual(meta['received_ns'],100)
    def test_writer_mid_copy_cannot_be_read(self):
        self.put(0,3);r=self.reader()
        fcntl.flock(self.fd,fcntl.LOCK_EX)
        try:
            self.put(0,6,b'B'*8)
            self.assertIsNone(r.latest())
        finally:fcntl.flock(self.fd,fcntl.LOCK_UN)
        self.assertEqual(r.latest()[0]['seq'],6)
    def test_reader_blocks_writer_on_same_inode(self):
        r=self.reader();fcntl.flock(r.fd,fcntl.LOCK_SH)
        try:
            with self.assertRaises(BlockingIOError):fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:fcntl.flock(r.fd,fcntl.LOCK_UN)
    def test_invalid_shape(self):
        self.put(0,1,b'B'*4);self.assertIsNone(self.reader().latest())
    def test_old_version_rejected(self):
        self.mm[4:8]=(1).to_bytes(4,'little')
        with self.assertRaises(ValueError):self.reader()
    def test_epoch_change_rejected(self):
        r=self.reader();self.mm[48:56]=(999).to_bytes(8,'little')
        with self.assertRaises(ValueError):r.latest()
    def test_truncated_layout(self):
        self.mm[12:16]=(16).to_bytes(4,'little')
        with self.assertRaises(ValueError):self.reader()
    def test_intrinsics(self):self.assertEqual(self.reader().intrinsics['fx'],400.)
if __name__=='__main__':unittest.main()
