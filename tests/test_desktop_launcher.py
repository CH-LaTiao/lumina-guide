import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import desktop_launcher as launcher


@unittest.skipUnless(os.name == 'posix', 'Jetson process locking requires Linux')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.source = self.root/'release'
        (self.source/'tools').mkdir(parents=True); (self.source/'tools/lan_access.py').touch()
        self.runtime = patch.object(launcher, 'RUNTIME', self.root/'runtime'); self.runtime.start(); self.addCleanup(self.runtime.stop)
        self.selection = patch('preview_control.select_source', return_value=(self.source, {})); self.selection.start(); self.addCleanup(self.selection.stop)
    def test_repeated_start_reuses_process(self):
        before = {'running':True,'process':{'pid':42,'code_root':str(self.source),'lan':True}}
        with patch.object(launcher,'control',return_value=before) as control, patch.object(launcher,'wait_ready',return_value={'state':'STREAMING'}):
            result, started = launcher.ensure_running()
        self.assertFalse(started); control.assert_called_once_with('status')
    def test_cold_start_sets_native_capture_and_lan(self):
        with patch.object(launcher,'control',return_value={'running':False}) as control, patch.object(launcher,'wait_ready',return_value={}):
            _, started = launcher.ensure_running()
        self.assertTrue(started)
        self.assertEqual(control.call_args.args, ('start','--deployed','--lan'))
        self.assertEqual(control.call_args.kwargs['env']['LUMINA_FRAME_SOURCE'],'shm')
    def test_upgrade_retains_existing_key_without_file_or_argv(self):
        before = {'running':True,'process':{'pid':42,'code_root':str(self.root/'old'),'version':'v0.8.7'}}
        with patch.object(launcher,'control',return_value=before) as control, patch.object(launcher,'owned_key',return_value='test-only-key'), patch.object(launcher,'wait_ready',return_value={}):
            launcher.ensure_running()
        self.assertEqual([x.args for x in control.call_args_list],[('status',),('stop',),('start','--deployed','--lan')])
        self.assertEqual(control.call_args.kwargs['env']['DASHSCOPE_API_KEY'],'test-only-key')
        self.assertNotIn('test-only-key',str(control.call_args.args))
    def test_failed_start_restores_previous_release(self):
        before = {'running':True,'process':{'pid':42,'code_root':str(self.root/'old'),'version':'v0.8.7'}}
        with patch.object(launcher,'control',return_value=before) as control, patch.object(launcher,'owned_key',return_value=None), patch.object(launcher,'wait_ready',side_effect=RuntimeError('failed')), patch.object(launcher.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):launcher.ensure_running()
        self.assertEqual(control.call_args.args,('start','--deployed'))
        self.assertEqual(run.call_args.args[0][-2:],['switch','v0.8.7'])


class DesktopTests(unittest.TestCase):
    def test_entry_uses_terminal_and_quote_paths(self):
        text = launcher.desktop_text(PurePosixPath('/home/example user/lumina/tools/desktop_launcher.py'))
        self.assertIn('Name=一键启动灵犀', text)
        self.assertIn('gnome-terminal', text)
        self.assertIn('"/home/example user/lumina/tools/desktop_launcher.py" launch', text)
        self.assertNotIn('DASHSCOPE', text)


if __name__ == '__main__': unittest.main()
