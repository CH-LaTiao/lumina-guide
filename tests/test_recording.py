import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from recording import Recorder, replay
from replay_recording import summarize

def frame(seq=0):
    return dict(sequence=seq, session_id='synthetic-fixture', host_mono_ns=seq+100,
                depth_us=seq, color_us=seq, width=2, height=1, color_width=2,
                color_height=1, depth_scale_mm=1.0, depth_format='Y16_LE',
                color_format='MJPG', aligned=False)
# JPEG-envelope fixture, not a decodable real camera image.
DEPTH=b'\x00\x00\xe8\x03'; COLOR=b'\xff\xd8\xff\xd9'
class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'capture.sqlite3'
    def writer(self, **kw):
        r=Recorder(self.path, **kw); self.addCleanup(r.close); return r
    def test_roundtrip(self):
        r=self.writer(); r.append(frame(),DEPTH,COLOR); r.append(frame(1),DEPTH,COLOR)
        self.assertEqual(list(replay(self.path))[0],(frame(),DEPTH,COLOR))
        self.assertEqual(summarize(self.path)['mean_nonzero_depth_ratio'],0.5)
    def test_committed_frames_readable_without_close(self):
        r=self.writer(); r.append(frame(),DEPTH,COLOR)
        self.assertEqual(len(list(replay(self.path))),1)
    def test_overwrite_refused(self):
        self.writer()
        with self.assertRaises(FileExistsError): Recorder(self.path)
    def test_bad_frame_not_committed(self):
        r=self.writer()
        with self.assertRaises(ValueError): r.append(frame(),DEPTH[:-1],COLOR)
        self.assertEqual(list(replay(self.path)),[])
    def test_quota(self):
        r=self.writer(max_payload_bytes=1)
        with self.assertRaises(ValueError): r.append(frame(),DEPTH,COLOR)
        self.assertEqual(list(replay(self.path)),[])
    def test_duplicate_and_cross_session(self):
        r=self.writer();r.append(frame(),DEPTH,COLOR)
        with self.assertRaises(ValueError):r.append(frame(),DEPTH,COLOR)
        m=frame(1);m['session_id']='different-boot'
        with self.assertRaises(ValueError):r.append(m,DEPTH,COLOR)
    def test_corruption_rejected(self):
        r=self.writer();r.append(frame(),DEPTH,COLOR)
        c=sqlite3.connect(self.path)
        try:
            with c:c.execute("UPDATE frames SET color=?",(b'bad',))
        finally:c.close()
        with self.assertRaises(ValueError):list(replay(self.path))
    def test_non_finite_scale(self):
        r=self.writer();m=frame();m['depth_scale_mm']=float('nan')
        with self.assertRaises(ValueError):r.append(m,DEPTH,COLOR)
    def test_uvc_padding_retained(self):
        r=self.writer();padded=COLOR+b'\x00'*128;r.append(frame(),DEPTH,padded)
        self.assertEqual(list(replay(self.path))[0][2],padded)

if __name__=='__main__':unittest.main()
