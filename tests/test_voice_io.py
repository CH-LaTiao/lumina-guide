import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from voice_io import devices,SOURCE,SINK,VoiceIO,PREWARM_PHRASES,CORE_PREWARM_PHRASES,EXTRA_PREWARM_PHRASES

class VoiceTests(unittest.TestCase):
    def test_prewarm_covers_current_detour_speech(self):
        for phrase in ('前方可走。','左侧可走。','停，改走右侧。','停，有台阶。',
                '停，感知不可用。','停，深度异常。','停，深度不足。',
                '前方近物，往左。','两侧近物，走中。'):
            self.assertIn(phrase,PREWARM_PHRASES)

    def test_wav_cache_synthesizes_each_phrase_once(self):
        with patch('voice_io.threading.Thread'), \
                patch('voice_io.subprocess.check_output',return_value=b'wav') as synth:
            voice=VoiceIO()
            self.assertEqual(voice._wav('测试短句',prefer_neural=False),b'wav')
            self.assertEqual(voice._wav('测试短句',prefer_neural=False),b'wav')
            self.assertEqual(synth.call_count,1)
            self.assertEqual(voice.read()['cache_phrases'],1)

    def test_prewarm_yields_immediately_to_pending_speech(self):
        with patch('voice_io.threading.Thread'):
            voice=VoiceIO()
        voice.priority=100;voice.stop_event.set()
        with patch.object(voice,'_wav') as synth:
            voice._prewarm()
        synth.assert_not_called()

    def test_core_prewarm_is_bounded_and_extras_are_disjoint(self):
        core=set(CORE_PREWARM_PHRASES);extra=set(EXTRA_PREWARM_PHRASES)
        self.assertTrue(core.issubset(PREWARM_PHRASES))
        self.assertTrue(extra.issubset(PREWARM_PHRASES))
        self.assertFalse(core & extra)
        self.assertEqual(core | extra,set(PREWARM_PHRASES))
        self.assertLessEqual(len(CORE_PREWARM_PHRASES),24)

    def test_extra_prewarm_requires_auto_playback(self):
        with patch('voice_io.threading.Thread'):
            voice=VoiceIO()
        with patch.object(voice,'_wav') as synth:
            self.assertFalse(voice._prewarm_batch(EXTRA_PREWARM_PHRASES[:1],require_auto=True))
            synth.assert_not_called()
            voice.auto=True
            self.assertTrue(voice._prewarm_batch(EXTRA_PREWARM_PHRASES[:1],require_auto=True))
            synth.assert_called_once()

    def test_urgent_uncached_speech_uses_local_engine(self):
        with patch('voice_io.threading.Thread'),patch('voice_io.subprocess.check_output',return_value=b'wav') as synth:
            voice=VoiceIO();voice.priority=100
            self.assertEqual(voice._wav('急停',prefer_neural=False),b'wav')
            self.assertIn('local_tts.py',synth.call_args.args[0][1])

    def test_nomachine_output_name_is_resolved_to_monitor_index(self):
        values={'sources':[{'name':SOURCE,'index':3},{'name':SINK+'.monitor','index':0}],
            'sinks':[{'name':SINK,'monitor_source':SINK+'.monitor'}],
            'source-outputs':[{'source':0,'properties':{'application.name':'NoMachine'}}]}
        with patch('voice_io.subprocess.check_output',side_effect=lambda cmd,**kw:json.dumps(values[cmd[-1]]).encode()):
            self.assertTrue(devices()['available'])
            values['source-outputs']=[]
            self.assertFalse(devices()['available'])
    def test_muted_source_unavailable(self):
        values={'sources':[{'name':SOURCE,'index':3,'mute':True},{'name':SINK+'.monitor','index':0}],
            'sinks':[{'name':SINK,'monitor_source':SINK+'.monitor'}],
            'source-outputs':[{'source':0,'properties':{'application.name':'NoMachine'}}]}
        with patch('voice_io.subprocess.check_output',side_effect=lambda cmd,**kw:json.dumps(values[cmd[-1]]).encode()):
            self.assertFalse(devices()['available'])
