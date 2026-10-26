"""Audio scheduling regressions; paplay and synthesis are isolated, never audible."""
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from live_view import State,guidance_is_current,queue_reviewed_answer
from voice_io import VoiceIO


class VoiceValidityTests(unittest.TestCase):
    def voice(self):
        with patch('voice_io.threading.Thread'):voice=VoiceIO()
        voice.route={'available':True}
        return voice

    def test_queued_direction_is_rejected_when_synthesis_outlives_sensor_evidence(self):
        voice=self.voice();usable=[True]
        voice.say('左侧可走。',50,valid=lambda:usable[0])
        text,generation,expires,valid,_=voice.pending
        # Simulate TTS returning after the evidence has expired.
        usable[0]=False
        with patch('voice_io.subprocess.Popen') as play:
            result=voice._play_wav(b'wav',generation,expires,valid,{})
        self.assertEqual(result,'INVALIDATED');play.assert_not_called()

    def test_playing_direction_is_stopped_without_a_new_guidance_tick(self):
        voice=self.voice();usable=[True]
        child=Mock();child.returncode=None;child.poll.side_effect=lambda:child.returncode
        def communicate(*args,**kwargs):
            if child.returncode is not None:return (None,None)
            usable[0]=False
            raise subprocess.TimeoutExpired('paplay',.1)
        child.communicate.side_effect=communicate
        child.terminate.side_effect=lambda:setattr(child,'returncode',-15)
        with patch('voice_io.subprocess.Popen',return_value=child):
            result=voice._play_wav(b'wav',voice.generation,time.monotonic()+2,lambda:usable[0],{})
        self.assertEqual(result,'INVALIDATED');child.terminate.assert_called_once()
        self.assertLessEqual(child.communicate.call_args_list[0].kwargs['timeout'],.1)

    def test_successful_playback_is_not_cut_by_queue_deadline(self):
        voice=self.voice();child=Mock();child.returncode=0;child.poll.return_value=0
        with patch('voice_io.subprocess.Popen',return_value=child):
            self.assertEqual(voice._play_wav(b'wav',voice.generation,time.monotonic()+2,lambda:True,{}),'FINISHED')
        child.communicate.assert_called_once();child.terminate.assert_not_called()

    def test_callback_error_fails_closed_before_playback(self):
        voice=self.voice()
        with patch('voice_io.subprocess.Popen') as play:
            self.assertEqual(voice._play_wav(b'wav',voice.generation,time.monotonic()+2,Mock(side_effect=RuntimeError),{}),'INVALIDATED')
        play.assert_not_called()

    def test_automatic_walk_transition_cancels_manual_answer_with_auto_off(self):
        voice=self.voice();voice.follow({'mode':'STANDING','event_id':1})
        voice.say('这里是很长的场景解释。');generation=voice.generation
        voice.follow({'mode':'WALKING','event_id':2,'speech':'停。','priority':90})
        self.assertIsNone(voice.pending);self.assertGreater(voice.generation,generation)
        self.assertTrue(voice.cancel_record.is_set());self.assertFalse(voice.auto)

    def test_first_walking_tick_also_cancels_pending_recording_or_speech(self):
        voice=self.voice();voice.say('解释。')
        voice.follow({'mode':'WALKING','event_id':2})
        self.assertIsNone(voice.pending);self.assertTrue(voice.cancel_record.is_set())

    def test_urgent_guidance_cancels_low_priority_manual_audio_with_auto_off(self):
        voice=self.voice();voice.follow({'mode':'STANDING','event_id':1})
        voice.say('场景说明。',25)
        voice.follow({'mode':'STANDING','event_id':2,'priority':90,'speech':'停。'})
        self.assertIsNone(voice.pending);self.assertFalse(voice.auto)

    def test_speech_change_with_same_event_id_is_not_ignored(self):
        voice=self.voice();voice.auto=True
        voice.follow({'mode':'WALKING','event_id':1,'speech':'左侧可走。','priority':50},valid=lambda:True)
        voice.follow({'mode':'WALKING','event_id':1,'speech':'停，感知不可用。','priority':90},valid=lambda:True)
        self.assertEqual(voice.pending[0],'停，感知不可用。')
        self.assertTrue(callable(voice.pending[3]))

    def test_published_state_withdraws_speech_after_decision_worker_stalls(self):
        expected={'mode':'WALKING','event_id':4,'speech':'左侧可走。','priority':50,'fresh':True}
        with patch('live_view.time.monotonic',return_value=10):
            state=State();state.update({'state':'STREAMING'},b'jpeg',{'captured':9.95})
            state.set_guidance(expected)
            self.assertTrue(guidance_is_current(state,expected))
        with patch('live_view.time.monotonic',return_value=11):
            # New camera data alone cannot keep a stopped decision loop alive.
            state.update({'state':'STREAMING'},b'jpeg',{'captured':10.95})
            self.assertFalse(guidance_is_current(state,expected))

    def test_reviewed_answer_guard_tracks_mode_and_query_lifetime(self):
        state=Mock();online=Mock();voice=Mock()
        guide={'mode':'STANDING','fresh':True}
        state.read.side_effect=lambda:({'guidance':dict(guide)},None)
        result={'state':'CANDIDATE','request_id':'a','plan':{
            'observations':[{'fact':'画面有标识'}],
            'next_step':{'kind':'observe','say':'请确认文字'}}}
        online.read.side_effect=lambda:dict(result)
        queue_reviewed_answer(state,online,voice)
        valid=voice.say.call_args.kwargs['valid'];self.assertTrue(valid())
        guide['priority']=90;self.assertFalse(valid());guide['priority']=50
        guide['mode']='WALKING';self.assertFalse(valid())
        guide['mode']='STANDING';result['state']='EXPIRED';self.assertFalse(valid())
        result['state']='CANDIDATE';result['request_id']='b';self.assertFalse(valid())


if __name__=='__main__':unittest.main()
