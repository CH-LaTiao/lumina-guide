"""Persistent, fully-local Piper Mandarin TTS worker."""
import io
import json
import os
import struct
import sys
import wave
from pathlib import Path

DEFAULT_MODEL=str(Path.home()/'lumina-models/piper/zh_CN-huayan-medium.onnx')


def load_voice(model):
    """Bound CPU inference so speech synthesis leaves capacity for perception."""
    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import PiperConfig
    options=ort.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.intra_op.allow_spinning','0')
    options.add_session_config_entry('session.inter_op.allow_spinning','0')
    with open(str(model)+'.json',encoding='utf-8') as source:
        config=PiperConfig.from_dict(json.load(source))
    return PiperVoice(session=ort.InferenceSession(str(model),sess_options=options,
        providers=['CPUExecutionProvider']),config=config)


def read_exact(stream,size):
    data=b''
    while len(data)<size:
        chunk=stream.read(size-len(data))
        if not chunk:return None
        data+=chunk
    return data


def main():
    model=os.environ.get('LUMINA_PIPER_MODEL',DEFAULT_MODEL)
    if not Path(model).is_file():raise SystemExit('Piper model unavailable')
    voice=load_voice(model)
    length_scale=float(os.environ.get('LUMINA_PIPER_LENGTH_SCALE','0.80'))
    while True:
        header=read_exact(sys.stdin.buffer,4)
        if header is None:return
        size=struct.unpack('>I',header)[0]
        if not 0<size<=4096:raise SystemExit('Invalid TTS request')
        text=read_exact(sys.stdin.buffer,size)
        if text is None:return
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            voice.synthesize(text.decode('utf-8'),wav,length_scale=length_scale)
        payload=output.getvalue()
        sys.stdout.buffer.write(struct.pack('>I',len(payload))+payload)
        sys.stdout.buffer.flush()


if __name__=='__main__':main()
