import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from live_view import GroundLine, State, depth_panel

class PreviewTests(unittest.TestCase):
    def test_ground_line_is_available_when_live_view_starts(self):
        self.assertTrue(callable(GroundLine))
    def test_stale_frame_hidden(self):
        s=State()
        with patch('live_view.time.monotonic',return_value=10):s.update({'state':'STREAMING'},b'jpeg')
        with patch('live_view.time.monotonic',return_value=10.6):
            status,jpeg=s.read();self.assertEqual(status['state'],'STALE');self.assertIsNone(jpeg)
    def test_fault_clears_frame(self):
        s=State();s.update({'state':'STREAMING'},b'jpeg');s.update({'state':'FAULT'})
        self.assertIsNone(s.read()[1])
    def test_invalid_depth_never_free_space(self):
        _,regions,coverage=depth_panel(np.zeros((480,640),np.uint16),1)
        self.assertEqual(coverage,0)
        self.assertTrue(all(r['status']=='UNKNOWN' and r['depth_p10_m'] is None for r in regions))
    def test_scale_applied(self):
        _,regions,_=depth_panel(np.full((480,640),1000,np.uint16),0.5)
        self.assertTrue(all(abs(r['depth_p10_m']-0.5)<1e-6 for r in regions))
    def test_peek_jpeg_returns_frame_and_state(self):
        s=State();s.update({'state':'STREAMING'},b'jpeg')
        frame,stamp,state_name=s.peek_jpeg()
        self.assertEqual(frame,b'jpeg');self.assertEqual(state_name,'STREAMING');self.assertGreater(stamp,0)
    def test_snapshot_cannot_survive_disconnect_or_warmup(self):
        import time
        s=State();snapshot={'captured':time.monotonic(),'frame_id':'session:1','jpeg':b'jpeg'}
        s.update({'state':'STREAMING'},b'jpeg',snapshot);self.assertIsNotNone(s.get_snapshot())
        s.update({'state':'RECONNECTING'});self.assertIsNone(s.get_snapshot())
        s.update({'state':'WARMING'},b'jpeg',snapshot);self.assertIsNone(s.get_snapshot())
        self.assertIsNone(s.read()[1])
    def test_rgb_snapshot_is_encoded_only_when_requested(self):
        import time
        s=State();rgb=np.zeros((16,16,3),np.uint8)
        s.update({'state':'STREAMING'},b'jpeg',
            {'captured':time.monotonic(),'frame_id':'session:1','rgb':rgb})
        value=s.get_snapshot()
        self.assertTrue(value['jpeg'].startswith(b'\xff\xd8'))
        self.assertNotIn('rgb',value)
        self.assertEqual(value['frame_id'],'session:1')
    def test_frame_age_includes_processing_time(self):
        s=State()
        with patch('live_view.time.monotonic',return_value=10):s.update({'state':'STREAMING'},b'jpeg',{'captured':9.7})
        with patch('live_view.time.monotonic',return_value=10):self.assertEqual(s.read()[0]['frame_age_ms'],300)
    def test_geometry_expires_before_preview_and_keeps_internal_state_immutable(self):
        s=State();g={'state':'READY','objects':[{'id':1}],'plane':{'d':1},'ground_state':'REFERENCE_MATCHED_CANDIDATE'}
        s.update({'state':'STREAMING','geometry':g},b'jpeg',{'captured':1.0})
        with patch('live_view.time.monotonic',return_value=1.201):
            value,jpg=s.read()
            self.assertEqual(value['geometry']['state'],'STALE')
            self.assertEqual(value['geometry']['objects'],[]);self.assertIsNone(value['geometry']['plane'])
            self.assertEqual(jpg,b'jpeg');self.assertEqual(g['state'],'READY')

    def test_geometry_display_holds_last_ready_rows_during_short_staleness(self):
        s=State();g={'state':'READY','objects':[{'id':1}],'plane':{'d':1},
            'ground_state':'UNKNOWN','cluster_count':2,'compute_ms':40}
        s.update({'state':'STREAMING','geometry':g,'geometry_display':dict(g,display_hold=False)},
            b'jpeg',{'captured':1.0})
        with patch('live_view.time.monotonic',return_value=1.201):
            value,jpg=s.read()
            self.assertEqual(value['geometry']['state'],'STALE')
            self.assertEqual(value['geometry_display']['state'],'READY')
            self.assertTrue(value['geometry_display']['display_hold'])
            self.assertGreaterEqual(value['geometry_display']['display_age_ms'],200)
            self.assertEqual(value['geometry_display']['objects'],[{'id':1}])
            self.assertIsNotNone(jpg)
if __name__=='__main__':unittest.main()
