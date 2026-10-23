import json,math,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from gait_calibration import GaitCalibrationManager


def wait(manager,states,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=manager.read()
        if value['state'] in states:return value
        time.sleep(.01)
    raise AssertionError(manager.read())


class GaitCalibrationTests(unittest.TestCase):
    def test_two_phase_calibration_writes_profile_and_hot_reload_file(self):
        calls=[]
        def collector(seconds):
            calls.append(seconds)
            if len(calls)==1:
                return [(i*.01,9.8+((i%3)-1)*.01,.01) for i in range(300)]
            return [(i*.01,9.8+.55*math.sin(2*math.pi*1.8*i*.01),.08) for i in range(600)]
        notes=[]
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'gait-calibration.json'
            manager=GaitCalibrationManager(output,collector=collector,notify=notes.append)
            with self.assertRaises(ValueError):manager.start_still(True,False)
            manager.start_still(True,True)
            self.assertTrue(wait(manager,{'STILL_READY','ERROR'})['still_complete'])
            manager.start_walk(True,True)
            result=wait(manager,{'COMPLETE','ERROR'})
            self.assertEqual(result['state'],'COMPLETE',result)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(output.read_text())['schema'],'lumina-gait-profile-1')
            self.assertTrue(any('步行' in note for note in notes))

    def test_walk_requires_still_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=GaitCalibrationManager(Path(tmp)/'x.json',collector=lambda s:[])
            with self.assertRaises(ValueError):manager.start_walk(True,True)


if __name__=='__main__':unittest.main()
