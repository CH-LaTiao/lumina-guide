"""Interruptible HTTP in a short-lived child process; no credential files."""
import base64
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request


def request(req, timeout, cancel=None):
    payload=json.dumps({'url':req.full_url,'headers':dict(req.header_items()),
        'data':base64.b64encode(req.data or b'').decode(),'timeout':timeout}).encode()
    with tempfile.TemporaryFile() as output:
        child=subprocess.Popen([sys.executable,__file__],stdin=subprocess.PIPE,
            stdout=output,stderr=subprocess.DEVNULL)
        def feed():
            try:
                child.stdin.write(payload);child.stdin.flush();child.stdin.close()
            except (BrokenPipeError,OSError,ValueError):pass
        writer=threading.Thread(target=feed,daemon=True);writer.start()
        end=time.monotonic()+timeout
        try:
            while child.poll() is None:
                if cancel is not None and cancel.is_set():raise InterruptedError('Cancelled')
                remaining=end-time.monotonic()
                if remaining<=0:raise TimeoutError('Deadline')
                time.sleep(min(.02,remaining))
            if child.returncode:raise ConnectionError('HTTP worker failed')
            output.seek(0)
            result=json.loads(output.read())
            if result.get('status'):
                raise urllib.error.HTTPError(req.full_url,result['status'],'Remote HTTP error',None,None)
            if result.get('error')=='TimeoutError':raise TimeoutError('Deadline')
            if result.get('error'):raise ConnectionError(result['error'])
            return base64.b64decode(result['body'],validate=True)
        finally:
            if child.poll() is None:child.kill()
            child.wait()


def worker():
    data=json.loads(sys.stdin.buffer.read(12*1024*1024))
    try:
        req=urllib.request.Request(data['url'],data=base64.b64decode(data['data']),headers=data['headers'])
        with urllib.request.urlopen(req,timeout=data['timeout']) as response:
            body=response.read(8*1024*1024+1)
        if len(body)>8*1024*1024:raise ValueError('Response too large')
        result={'body':base64.b64encode(body).decode()}
    except urllib.error.HTTPError as e:result={'status':e.code}
    except Exception as e:result={'error':type(e).__name__}
    sys.stdout.write(json.dumps(result))


if __name__=='__main__':worker()
