import http.client
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from lan_access import AccessPolicy, AccessHandlerMixin
from status_console import access_banner


class PolicyTests(unittest.TestCase):
    def test_direct_lan_needs_no_cookie_or_code(self):
        policy = AccessPolicy(8765, True)
        self.assertTrue(policy.authorized('127.0.0.1'))
        self.assertTrue(policy.authorized('10.0.0.10'))
        self.assertFalse(AccessPolicy(8765).authorized('10.0.0.10'))
    def test_host_must_match_accepted_interface_not_attacker_dns(self):
        policy = AccessPolicy(8765, True)
        self.assertTrue(policy.valid_host('10.0.0.42:8765', '10.0.0.42'))
        self.assertTrue(policy.valid_host('localhost:8765', '127.0.0.1'))
        for host in ('evil.example:8765', '10.0.0.42:8766', 'evil@10.0.0.42:8765',
                     '10.0.0.42:8765/path', '10.0.0.42:bad', None):
            self.assertFalse(policy.valid_host(host, '10.0.0.42'), host)
        self.assertFalse(policy.valid_host('127.0.0.1:8765', '10.0.0.42'))
    def test_console_shows_direct_url(self):
        self.assertIsNone(access_banner({}))
        text=access_banner({'network':{'lan':True,'urls':['http://10.0.0.42:8765']}})
        self.assertIn('http://10.0.0.42:8765',text)
        self.assertIn('无需访问码',text)


class HttpTests(unittest.TestCase):
    def setUp(self):
        policy = AccessPolicy(0, True)
        class Handler(AccessHandlerMixin, BaseHTTPRequestHandler):
            access_policy = policy
            def setup(self):
                super().setup()
                self.client_address = ('10.0.0.10', self.client_address[1])
            def log_message(self, *args): pass
            def do_GET(self):
                if self.access_guard(): self.access_reply(200, b'live-frame')
            def do_POST(self):
                if self.access_guard(): self.access_reply(200, b'controlled')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.policy = policy; policy.port = self.server.server_port
        self.origin = f'http://127.0.0.1:{policy.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.policy.port, timeout=3)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: client.close()
    def test_all_lan_resources_and_controls_work_without_login(self):
        for path in ('/','/status','/frame.jpg','/stream.mjpg','/reviews.csv','/live_view.js'):
            code,headers,body=self.request('GET',path)
            self.assertEqual(code,200,path);self.assertEqual(body,b'live-frame')
            self.assertNotIn('Set-Cookie',headers)
        self.assertEqual(self.request('POST','/guidance/mode','{}',{'Origin':self.origin})[0],200)
    def test_cross_site_and_dns_rebinding_remain_blocked(self):
        for origin in ('http://evil.example',None):
            headers={'Origin':origin} if origin else {}
            self.assertEqual(self.request('POST','/guidance/mode','{}',headers)[0],403)
        self.assertEqual(self.request('GET','/status',headers={'Host':'evil.example:8765'})[0],403)


if __name__ == '__main__': unittest.main()
