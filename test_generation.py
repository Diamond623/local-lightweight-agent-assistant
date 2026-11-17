import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import app
import generation


def sse(*events,done=True):
    payload=b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)
    return io.BytesIO(payload+(b'data: [DONE]\n\n' if done else b''))

class StreamTests(unittest.TestCase):
    def test_cancel_while_model_is_silent_or_has_not_sent_headers(self):
        for headers in (False, True):
            with self.subTest(headers=headers):
                entered, release, stop = threading.Event(), threading.Event(), threading.Event()
                class SilentModel(BaseHTTPRequestHandler):
                    def log_message(self, *args): pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        if headers:
                            self.send_response(200); self.end_headers(); self.wfile.flush()
                        entered.set()
                        release.wait(3)
                server = ThreadingHTTPServer(('127.0.0.1', 0), SilentModel)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                errors, finished = [], threading.Event()
                def run():
                    try:
                        generation.completion(f'http://127.0.0.1:{server.server_port}/', {}, lambda _: None, stop.is_set)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        finished.set()
                worker = threading.Thread(target=run, daemon=True); worker.start()
                try:
                    self.assertTrue(entered.wait(1))
                    stop.set()
                    self.assertTrue(finished.wait(.8), 'Stop must not wait for the model socket timeout')
                    self.assertIsInstance(errors[0], generation.Cancelled)
                finally:
                    release.set(); worker.join(1); server.shutdown(); server.server_close()

    def test_search_planner_can_be_cancelled_before_headers(self):
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        def open_later(*args, **kwargs):
            entered.set(); release.wait(2)
            return io.BytesIO(b'{}')
        errors, finished = [], threading.Event()
        def run():
            try: generation.request_json('http://127.0.0.1/test', {}, stop.is_set)
            except Exception as error: errors.append(error)
            finally: finished.set()
        with patch.object(generation.urllib.request, 'urlopen', side_effect=open_later):
            worker=threading.Thread(target=run);worker.start()
            try:
                self.assertTrue(entered.wait(1));stop.set()
                self.assertTrue(finished.wait(.8));self.assertIsInstance(errors[0],generation.Cancelled)
            finally: release.set();worker.join(1)

    def test_deltas_are_joined_without_reasoning(self):
        events=[]
        source=sse({'choices':[{'delta':{'reasoning_content':'private reasoning'}}]}, {'choices':[{'delta':{'content':'你好'}}]}, {'choices':[{'delta':{'content':'世界'},'finish_reason':'stop'}]})
        with patch.object(generation.urllib.request,'urlopen',return_value=source):
            result=generation.completion('http://127.0.0.1:8082/v1/chat/completions',{},events.append,lambda:False)
        self.assertEqual(result['choices'][0]['message']['content'],'你好世界')
        self.assertNotIn('private',str(events))
    def test_premature_eof_is_not_success(self):
        with patch.object(generation.urllib.request,'urlopen',return_value=sse({'choices':[{'delta':{'content':'半句'}}]},done=False)):
            with self.assertRaisesRegex(RuntimeError,'中断'):
                generation.completion('http://127.0.0.1/test',{},lambda _:None,lambda:False)
    def test_done_without_finish_reason_does_not_save_a_partial_answer(self):
        with patch.object(generation.urllib.request,'urlopen',return_value=sse({'choices':[{'delta':{'content':'半句'}}]})):
            with self.assertRaisesRegex(RuntimeError,'中断'):
                generation.completion('http://127.0.0.1/test',{},lambda _:None,lambda:False)
    def test_cancel_closes_upstream(self):
        stop=threading.Event();source=sse({'choices':[{'delta':{'content':'半句'}}]})
        with patch.object(generation.urllib.request,'urlopen',return_value=source):
            with self.assertRaises(generation.Cancelled):
                generation.completion('http://127.0.0.1/test',{},lambda _:stop.set(),stop.is_set)
        self.assertTrue(source.closed)

class StreamApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(app,'DATA',Path(self.temp.name));self.patch.start()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        _,self.cookie=app.auth.api(app.DATA,'register',{'username':'tester','display_name':'测试','password':'test-password-123'},None,'')
        self.sid=self.post('new',{})['id']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.patch.stop();self.temp.cleanup()
    def request(self,path,data):
        return urllib.request.Request(self.base+'/api/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':app.TOKEN,'Cookie':self.cookie})
    def post(self,path,data):
        with urllib.request.urlopen(self.request(path,data),timeout=5) as response:return json.load(response)
    def test_preferences_persist_and_reject_invalid_values(self):
        self.post('preferences',{'mode':'text','online':True,'auto_start':False})
        self.assertEqual(self.post('preferences',{}),{'mode':'text','online':True,'auto_start':False})
        with self.assertRaises(urllib.error.HTTPError) as error:self.post('preferences',{'online':'yes'})
        error.exception.close()
        self.assertTrue(self.post('preferences',{})['online'])
    def test_ndjson_works_and_cancellation_does_not_save_partial(self):
        def fake(url,payload,emit,cancelled,timeout=240):
            emit({'type':'delta','text':'半句'})
            deadline=time.monotonic()+3
            while not cancelled() and time.monotonic()<deadline:time.sleep(.005)
            if cancelled():raise generation.Cancelled('已停止生成')
            raise RuntimeError('test cancellation was not received')
        with patch.object(app,'model_info',return_value='test'),patch.object(app.generation,'completion',side_effect=fake):
            with urllib.request.urlopen(self.request('chat',{'session':self.sid,'text':'解释归结法','mode':'gemma','online':True,'stream':True,'request':'this-job'}),timeout=5) as response:
                events=[]
                for line in response:
                    event=json.loads(line);events.append(event)
                    if event['type']=='delta':
                        self.assertFalse(self.post('cancel',{'session':self.sid,'request':'wrong-job'})['cancelled'])
                        self.assertTrue(self.post('cancel',{'session':self.sid,'request':'this-job'})['cancelled'])
                self.assertTrue(events[-1]['cancelled'])
        self.assertFalse(app.CHAT_LOCK.locked())
        self.assertEqual(self.post('messages',{'session':self.sid}),[])
    def test_direct_remember_stream_saves_complete_result(self):
        with patch.object(app,'model_info',return_value=None):
            with urllib.request.urlopen(self.request('chat',{'session':self.sid,'text':'记住：用中文简短回答','mode':'gemma','online':True,'stream':True}),timeout=5) as response:
                events=[json.loads(line) for line in response]
        self.assertEqual(events[-1]['type'],'result')
        self.assertIn('已记住',events[-1]['answer'])
        self.assertEqual(len(self.post('messages',{'session':self.sid})),2)
        self.assertEqual(len(self.post('memories',{})),1)

if __name__=='__main__':unittest.main()
