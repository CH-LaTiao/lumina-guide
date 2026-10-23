import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from watchdog import decide


class WatchdogTests(unittest.TestCase):
    def test_healthy_shm_mode_needs_no_action(self):
        self.assertEqual(decide(True,True,True),[])

    def test_dead_frame_server_is_restarted_first(self):
        self.assertEqual(decide(True,False,True),['frame-start'])

    def test_dead_preview_only_starts_preview(self):
        self.assertEqual(decide(False,True,True),['start'])

    def test_both_dead_restarts_frame_then_preview(self):
        self.assertEqual(decide(False,False,True),['frame-start','start'])

    def test_sdk_mode_ignores_missing_frame_server(self):
        self.assertEqual(decide(True,False,False),[])
        self.assertEqual(decide(False,False,False),['start'])


if __name__=='__main__':unittest.main()
