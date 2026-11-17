import contextvars
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import app
import auth
import welcome

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(app,'DATA',Path(self.tmp.name));self.patch.start()
        self.context=auth.CURRENT.set(None)
    def tearDown(self):
        auth.CURRENT.reset(self.context);self.patch.stop();self.tmp.cleanup()
    def register(self,name):
        return auth.api(app.DATA,'register',{'username':name,'password':'password-12345','display_name':name},None,'')
    def test_password_and_revocation(self):
        result,cookie=self.register('owner');user=auth.resolve(app.DATA,cookie)
        self.assertTrue(user['owner']);self.assertNotIn('password',str(user));self.assertIn('HttpOnly',cookie)
        with auth.database(app.DATA) as db:
            row=db.execute('SELECT * FROM users').fetchone();self.assertNotEqual(row['password_hash'],'password-12345')
        with self.assertRaises(ValueError):auth.api(app.DATA,'login',{'username':'owner','password':'wrong-123456'},None,'')
        auth.api(app.DATA,'password',{'old_password':'password-12345','password':'new-password-123'},user,cookie)
        self.assertIsNone(auth.resolve(app.DATA,cookie))
        _,new=auth.api(app.DATA,'login',{'username':'owner','password':'new-password-123'},None,'')
        auth.api(app.DATA,'logout',{},auth.resolve(app.DATA,new),new);self.assertIsNone(auth.resolve(app.DATA,new))
    def test_isolation_legacy_names_and_threads(self):
        with app.database() as db:db.execute("INSERT INTO memories(content,created) VALUES('legacy-secret','now')")
        owner,_=self.register('owner');guest,_=self.register('guest');owner=owner['user'];guest=guest['user']
        auth.CURRENT.set(owner)
        with app.database() as db:self.assertEqual(db.execute('SELECT content FROM memories').fetchone()[0],'legacy-secret')
        auth.CURRENT.set(guest)
        with app.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],0)
        welcome.save('Guest Name');self.assertEqual(welcome.read()['nickname'],'Guest Name')
        ctx=contextvars.copy_context();results=[]
        def read():
            with app.database() as db:results.append(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0])
        thread=threading.Thread(target=lambda:ctx.run(read));thread.start();thread.join();self.assertEqual(results,[0])
        auth.CURRENT.set(owner)
        with app.database() as db:self.assertIsNone(db.execute("SELECT value FROM preferences WHERE key='welcome_nickname'").fetchone())
    def test_login_throttle(self):
        self.register('owner')
        for _ in range(5):
            with self.assertRaisesRegex(ValueError,'不正确'):auth.api(app.DATA,'login',{'username':'owner','password':'wrong-123456'},None,'')
        with self.assertRaisesRegex(ValueError,'一分钟'):auth.api(app.DATA,'login',{'username':'owner','password':'password-12345'},None,'')
    def test_http_gate_cookie_and_cross_account(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        def post(path,obj={},cookie='',token=app.TOKEN):
            req=urllib.request.Request(base+'/api/'+path,json.dumps(obj).encode(),headers={'X-Local-Token':token,'Content-Type':'application/json','Cookie':cookie})
            try:r=urllib.request.urlopen(req)
            except urllib.error.HTTPError as error:r=error
            with r:return r.status,json.load(r),r.headers.get('Set-Cookie')
        try:
            self.assertIn('欢迎回来',urllib.request.urlopen(base).read().decode())
            self.assertEqual(post('sessions')[0],401);self.assertEqual(post('auth/state',token='bad')[0],403)
            status,_,owner=post('auth/register',{'username':'owner','display_name':'主人','password':'password-12345'})
            self.assertEqual(status,200);sid=post('new',cookie=owner)[1]['id'];post('remember',{'text':'private-owner'},owner)
            _,_,guest=post('auth/register',{'username':'guest','display_name':'客人','password':'password-12345'})
            self.assertEqual(post('location-city',{'city':'珠海'},owner)[1],{'city':'珠海'})
            self.assertEqual(post('location-city',cookie=guest)[1],{'city':''})
            self.assertEqual(post('location-city',{'city':'22.3,113.5'},owner)[0],400)
            self.assertEqual(post('location-city',{'city':''},owner)[1],{'city':''})
            self.assertEqual(post('sessions',cookie=guest)[1],[]);self.assertEqual(post('memories',cookie=guest)[1],[])
            self.assertEqual(post('messages',{'session':sid},guest)[1],[])
            self.assertEqual(post('install-task-runner',cookie=guest)[0],403)
            self.assertEqual(post('archive-session',{'session':sid,'archived':True},guest)[0],400)
            self.assertEqual(post('archive-session',{'session':sid,'archived':True},owner)[0],200)
            self.assertEqual(post('sessions',cookie=owner)[1],[])
            archived=post('sessions',{'archived':True},owner)[1]
            self.assertEqual(archived[0]['id'],sid)
            self.assertEqual(post('rename-session',{'session':sid,'title':'归档后改名'},owner)[0],200)
            self.assertEqual(post('sessions',{'archived':True},owner)[1][0]['title'],'归档后改名')
            self.assertEqual(post('archive-session',{'session':sid,'archived':False},owner)[0],200)
            post('new',cookie=guest)
            self.assertEqual(len(post('sessions',cookie=owner)[1]),1)
            post('auth/logout',cookie=owner);self.assertEqual(post('sessions',cookie=owner)[0],401)
            self.assertEqual(post('sessions',cookie=guest)[0],200)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
