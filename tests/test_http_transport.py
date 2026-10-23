import sys,threading,time,unittest,urllib.request,subprocess
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from http_transport import request

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.entered=threading.Event();self.release=threading.Event();outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                outer.entered.set();outer.release.wait(3)
                try:
                    self.send_response(200);self.end_headers();self.wfile.write(b'{}')
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}',data=b'{}')
    def tearDown(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(1)
    def test_blocked_body_kills_worker_at_deadline(self):
        real=subprocess.Popen;children=[]
        def spawn(*a,**kw):
            child=real(*a,**kw);children.append(child);return child
        started=time.monotonic()
        with patch('http_transport.subprocess.Popen',side_effect=spawn):
            with self.assertRaises(TimeoutError):request(self.req,.4)
        self.assertLess(time.monotonic()-started,1.2)
        self.assertTrue(all(c.poll() is not None for c in children))
    def test_cancel_reaps_worker(self):
        cancel=threading.Event();errors=[]
        def run():
            try:request(self.req,3,cancel)
            except Exception as e:errors.append(e)
        t=threading.Thread(target=run);t.start()
        self.assertTrue(self.entered.wait(2));cancel.set();t.join(1)
        self.assertFalse(t.is_alive());self.assertIsInstance(errors[0],InterruptedError)

    def test_request_larger_than_pipe_buffer(self):
        self.release.set()
        payload=b'x'*(256*1024)
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}',data=payload)
        result=request(req,3)
        self.assertEqual(result,b'{}')
if __name__=='__main__':unittest.main()
