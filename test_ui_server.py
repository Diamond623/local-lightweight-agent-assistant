"""Development-only local fixture. Uses synthetic replies and a temporary DB."""
import argparse
import datetime
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import app

class FixtureModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        body=json.dumps({'data':[{'id':'gemma-4-12b','architecture':{'input_modalities':['text','image']}}]}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        question=payload['messages'][-1]['content']
        if payload.get('max_tokens')==250:
            current=json.loads(question)['current_question']
            places=[city for city in ['广州','珠海','成都'] if city in current]
            response=json.dumps({'choices':[{'message':{'content':json.dumps({'places':places,'topic':''})},'finish_reason':'stop'}]}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response);return
        image=isinstance(question,list)
        if image:question=question[0]['text']
        if '长回答' in question:answer='用于检查停止按钮的长回答。'*180
        elif '推广词' in question:answer='Aurora，让灵感有下文。'
        elif '代码' in question:answer='**代码示例**\n\n```c\nprintf("hello");\n```\n\n<script>window.injected=true</script>'
        elif '名字' in question or '我是谁' in question:
            answer='你的名字是星河。' if '我叫星河' in str(payload['messages']) else '当前没有你的姓名资料。'
        elif image:answer='测试图片已经传给本地模型，本轮可以继续追问。'
        elif '附件：' in question:answer='文档测试内容：'+('AURORA_TEST_DOCUMENT' if 'AURORA_TEST_DOCUMENT' in question else '没有找到文档标记')
        elif '广州' in question:answer='广州的测试预报：多云，21～30℃。[1]'
        elif '珠海' in question:answer='珠海的测试预报：多云，24～31℃。[1]'
        else:answer='这是用于界面验收的**本地测试回复**。\n\n- 不依赖外网\n- 支持逐字显示'
        if not payload.get('stream'):
            body=json.dumps({'choices':[{'message':{'content':answer},'finish_reason':'stop'}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers();self.close_connection=True
        try:
            for i in range(0,len(answer),6):
                part={'choices':[{'delta':{'content':answer[i:i+6]}}]}
                self.wfile.write(b'data: '+json.dumps(part).encode()+b'\n\n');self.wfile.flush();time.sleep(.025)
            self.wfile.write(b'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]}).encode()+b'\n\ndata: [DONE]\n\n');self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18765);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='aurora-ui-fixture-') as folder:
        app.DATA=Path(folder)
        model=ThreadingHTTPServer(('127.0.0.1',0),FixtureModel)
        app.PORTS={mode:model.server_port for mode in ('text','vision','gemma')}
        def search(query,places=None,cancelled=None):
            if '推广词' in query:raise AssertionError('Creative task incorrectly routed into search')
            name=(places or ['测试地点'])[0]
            return [{'title':name+'测试天气网页','url':'https://example.org/weather','snippet':'','content':name+' '+datetime.date.today().isoformat()+' 今天 多云 25℃','read':True}]
        app.search_web=search
        threading.Thread(target=model.serve_forever,daemon=True).start()
        server=ThreadingHTTPServer(('127.0.0.1',args.port),app.Handler)
        print(f'UI FIXTURE http://127.0.0.1:{server.server_port}/ (synthetic model, temporary data)',flush=True)
        try:server.serve_forever()
        finally:server.server_close();model.shutdown();model.server_close()

if __name__=='__main__':main()
