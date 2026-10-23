import argparse
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from calibrate_mount import calibrate, make_payloads


def values(**changes):
    result={'height_m':1.1,'pitch_down_deg':8,'camera_forward_m':.12,
            'camera_right_m':-.03,'yaw_deg':5,'required_width_m':.8}
    result.update(changes)
    return argparse.Namespace(**result)


class CalibrateMountTests(unittest.TestCase):
    def test_builds_explicit_measured_bench_and_body_payloads(self):
        bench,body=make_payloads(values())
        self.assertEqual(bench,{'height_m':1.1,'pitch_down_deg':8.,
            'measured':True,'stationary':True})
        self.assertEqual(body['camera_forward_m'],.12)
        self.assertEqual(body['required_passage_width_m'],.8)
        self.assertTrue(body['measured'] and body['stationary'])

    def test_rejects_values_outside_documented_ranges(self):
        for change in ({'height_m':.1},{'pitch_down_deg':90},{'yaw_deg':60},
                       {'required_width_m':.2},{'camera_right_m':.8}):
            with self.assertRaises(ValueError):make_payloads(values(**change))

    def test_local_http_sequence_reaches_body_ready(self):
        state={'ground':'UNKNOWN','body':'UNSET','ready':False,'posts':[],'reference':None}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                value={'state':'STREAMING','geometry':{'ground_state':state['ground'],
                    'body_calibration':{'state':state['body']},'reference':state['reference']},
                    'readiness':{'ready_for_body_guidance':state['ready']}}
                data=json.dumps(value).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def do_POST(self):
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length))
                state['posts'].append((self.path,payload))
                response={'state':'SET'}
                if self.path=='/geometry/reference':
                    state['ground']='REFERENCE_MATCHED_CANDIDATE'
                    state['reference']={'sha256':'bench-new','source':'BENCH'}
                    response['sha256']='bench-new'
                elif self.path=='/body/calibration' and payload.get('clear') is True:
                    state['body']='UNSET';state['ready']=False
                elif self.path=='/body/calibration':
                    state['body']='ACTIVE';state['ready']=True
                data=json.dumps(response).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            bench,body=make_payloads(values())
            result=calibrate(f'http://127.0.0.1:{server.server_port}',1,bench,body)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=2)
        self.assertEqual(result['state'],'CALIBRATED')
        self.assertEqual(result['body_calibration']['state'],'ACTIVE')
        self.assertFalse(result['movement_allowed'])
        self.assertEqual([path for path,_ in state['posts']],
                         ['/body/calibration','/geometry/reference','/body/calibration'])
        self.assertTrue(state['posts'][0][1]['clear'])


if __name__=='__main__':unittest.main()
