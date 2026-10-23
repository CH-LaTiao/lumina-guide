import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from tts_benchmark import p95,wav_seconds


class TtsBenchmarkTests(unittest.TestCase):
    def test_p95_uses_nearest_rank(self):
        self.assertEqual(p95([.1,.2,.3]),.3)
        self.assertEqual(p95([.5]),.5)
        self.assertEqual(p95([.2,.1,.5,.3]),.5)

    def test_wav_seconds_uses_actual_frame_rate(self):
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b'\0\0'*8000)
        self.assertEqual(wav_seconds(output.getvalue()),.5)


if __name__=='__main__':unittest.main()
