"""Generate Mandarin WAV on stdout using the installed eSpeak NG library.

Runs in an isolated process: a native synthesizer failure cannot stop perception.
"""
import ctypes as c
import ctypes.util
import io
import sys
import wave


def synthesize(text):
    if not isinstance(text,str) or not 1<=len(text)<=500:raise ValueError('Invalid speech length')
    name=ctypes.util.find_library('espeak-ng')
    if not name:raise RuntimeError('eSpeak NG unavailable')
    lib=c.CDLL(name)
    lib.espeak_Initialize.argtypes=[c.c_int,c.c_int,c.c_char_p,c.c_int]
    rate=lib.espeak_Initialize(2,0,None,0x8000)
    if rate<=0:raise RuntimeError('TTS initialization failed')
    chunks=[]
    callback_type=c.CFUNCTYPE(c.c_int,c.POINTER(c.c_short),c.c_int,c.c_void_p)
    @callback_type
    def receive(data,count,events):
        if data and count>0:chunks.append(c.string_at(data,count*2))
        return 0
    lib.espeak_SetSynthCallback.argtypes=[callback_type];lib.espeak_SetSynthCallback(receive)
    lib.espeak_SetVoiceByName.argtypes=[c.c_char_p]
    if lib.espeak_SetVoiceByName(b'cmn')!=0:raise RuntimeError('Mandarin voice unavailable')
    lib.espeak_SetParameter(1,205,0)
    lib.espeak_Synth.argtypes=[c.c_char_p,c.c_size_t,c.c_uint,c.c_int,c.c_uint,c.c_uint,c.c_void_p,c.c_void_p]
    data=text.encode('utf-8')+b'\0'
    if lib.espeak_Synth(data,len(data),0,1,0,1,None,None)!=0:raise RuntimeError('Synthesis failed')
    lib.espeak_Synchronize();lib.espeak_Terminate()
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(b''.join(chunks))
    return output.getvalue()


if __name__=='__main__':sys.stdout.buffer.write(synthesize(sys.stdin.read(501)))
