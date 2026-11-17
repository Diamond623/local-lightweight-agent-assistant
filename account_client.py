"""Optional central-account bridge. Only account fields leave the local client."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import auth

ROOT=Path(__file__).resolve().parent
LIVE={}
LOCK=threading.Lock()


def service_url():
    value=os.environ.get('AURORA_ACCOUNT_URL','')
    config=ROOT/'account-service.json'
    if not value and config.exists():value=json.loads(config.read_text()).get('url','')
    if not value:return ''
    parts=urlsplit(value)
    if (parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname=='127.0.0.1')) or not parts.netloc or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('账号服务地址须为 HTTPS 域名，或本机 http://127.0.0.1:端口')
    return value.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('账号服务不允许重定向，请填写最终地址')


def request(action,obj,cookie=''):
    url=service_url();opener=urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url+'/',timeout=8) as response:page=response.read(300000).decode()
        match=re.search(r"const token='([^']+)'",page)
        # Logged-in non-admin landing has no token; bootstrap token anonymously above.
        if not match:raise ValueError('账号服务返回格式不正确')
        req=urllib.request.Request(url+'/api/auth/'+action,json.dumps(obj).encode(),headers={'Content-Type':'application/json','X-Local-Token':match[1],'Cookie':cookie})
        with opener.open(req,timeout=12) as response:return json.load(response),response.headers.get('Set-Cookie')
    except urllib.error.HTTPError as error:
        with error:
            try:message=json.load(error).get('error','账号服务请求失败')
            except Exception:message='账号服务请求失败'
        raise ValueError(message) from None
    except (urllib.error.URLError,TimeoutError,OSError):raise ValueError('无法连接统一账号服务，请检查服务是否启动与网络连接') from None


def scoped(user):
    if not user:return None
    return {**user,'owner':0,'remote_owner':bool(user['owner']),'service':hashlib.sha256(service_url().encode()).hexdigest()[:24]}


def resolve(base,cookie):
    if not service_url():return auth.resolve(base,cookie)
    if not auth.cookie_token(cookie):return None
    user=scoped(request('state',{},cookie)[0]['user'])
    if user:
        with LOCK:LIVE[cookie]=user
    else:
        with LOCK:LIVE.pop(cookie,None)
    return user


def api(base,action,obj,user,cookie):
    if not service_url():return auth.api(base,action,obj,user,cookie)
    data,new_cookie=request(action,obj,cookie)
    if 'user' in data:data['user']=scoped(data['user'])
    data['setup']=False
    if action in ('logout','password'):
        with LOCK:LIVE.pop(cookie,None)
    # The desktop bridge itself serves HTTP loopback, even when upstream uses TLS.
    if new_cookie:new_cookie=new_cookie.replace('; Secure','')
    return data,new_cookie


def live_users(base):
    with LOCK:cookies=list(LIVE)
    for cookie in cookies:
        try:
            user=resolve(base,cookie)
            if user:yield user
        except ValueError:continue
