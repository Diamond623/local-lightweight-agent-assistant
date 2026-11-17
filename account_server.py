"""Central account service. Loopback by default; chat/model APIs are never served."""
import argparse
import json
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
import auth

ROOT=Path(__file__).resolve().parent

class AccountHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,obj,status=200,cookie=None,html=False):
        body=obj.encode() if html else json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        if cookie:self.send_header('Set-Cookie',cookie+('; Secure' if self.server.public_origin else ''))
        self.end_headers();self.wfile.write(body)
    def valid_host(self):
        return self.headers.get('Host')==self.server.allowed_host
    def do_GET(self):
        if not self.valid_host():return self.reply({'error':'Host rejected'},403)
        if self.path!='/':return self.reply({'error':'Not found'},404)
        user=auth.resolve(self.server.data,self.headers.get('Cookie',''))
        page='admin.html' if user and user['owner'] else 'login.html'
        if user and not user['owner']:
            return self.reply('<!doctype html><meta charset="utf-8"><title>Aurora 账号</title><p>你已登录 Aurora 统一账号服务。请返回 Aurora 客户端使用。</p>',html=True)
        source=(ROOT/page).read_text().replace('__TOKEN__',self.server.token)
        source=source.replace('<a id="backtochat" href="/">返回对话</a>', '<span>请在 Aurora 客户端继续对话</span>')
        source=source.replace('第一个账号为本机管理员，将保留这台电脑原来的聊天、记忆和任务。请由电脑主人创建。','请由服务所有者创建第一个管理员账号。')
        self.reply(source,html=True)
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Local-Token')!=self.server.token:
            return self.reply({'error':'Unauthorized'},403)
        origin=self.headers.get('Origin')
        expected=self.server.public_origin or 'http://'+self.server.allowed_host
        if origin and origin!=expected:return self.reply({'error':'Origin rejected'},403)
        if not self.path.startswith('/api/auth/'):return self.reply({'error':'Not found'},404)
        if not self.server.slots.acquire(blocking=False):return self.reply({'error':'服务繁忙，请稍后重试'},429)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=8192:raise ValueError('请求太大')
            obj=json.loads(self.rfile.read(length) or b'{}')
            user=auth.resolve(self.server.data,self.headers.get('Cookie',''))
            result,cookie=auth.api(self.server.data,self.path.rsplit('/',1)[-1],obj,user,self.headers.get('Cookie',''))
            self.reply(result,cookie=cookie)
        except (ValueError,KeyError,TypeError) as error:self.reply({'error':str(error)},400)
        finally:self.server.slots.release()


def create_server(data,port=8766,public_origin=''):
    if public_origin:
        parsed=urlsplit(public_origin)
        if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('公开地址必须为 HTTPS 域名，例如 https://accounts.example.com')
        if not auth.accounts(data):raise ValueError('请先在本机创建管理员账号，再配置公开地址')
    server=ThreadingHTTPServer(('127.0.0.1',port),AccountHandler)
    server.data=data;server.token=secrets.token_urlsafe(32);server.public_origin=public_origin
    server.allowed_host=urlsplit(public_origin).netloc if public_origin else '127.0.0.1:'+str(server.server_port)
    server.slots=threading.BoundedSemaphore(8)
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--public-origin',default='',help='Only behind a configured HTTPS reverse proxy; service always binds loopback')
    args=parser.parse_args()
    server=create_server(args.data,args.port,args.public_origin)
    print('Aurora 统一账号服务：http://127.0.0.1:'+str(server.server_port)+'/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
