import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import account_server
import account_client
import auth

class CentralAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.server=account_server.create_server(self.base,0);self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.env=patch.dict('os.environ',{'AURORA_ACCOUNT_URL':self.url});self.env.start()
    def tearDown(self):
        self.env.stop();self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup();account_client.LIVE.clear()
    def register(self,name):return account_client.request('register',{'username':name,'display_name':name,'password':'password-12345'})
    def test_management_revokes_and_registration_switch(self):
        owner,oc=self.register('owner');guest,gc=self.register('guest')
        with self.assertRaisesRegex(ValueError,'仅管理员'):account_client.request('admin-list',{},gc)
        data,_=account_client.request('admin-list',{},oc)
        self.assertEqual(len(data['users']),2);self.assertNotIn('password_hash',str(data));self.assertNotIn('salt',str(data))
        account_client.request('admin-disable',{'id':guest['user']['id'],'disabled':True},oc)
        self.assertIsNone(account_client.resolve(self.base,gc))
        with self.assertRaisesRegex(ValueError,'不正确'):account_client.request('login',{'username':'guest','password':'password-12345'})
        account_client.request('admin-disable',{'id':guest['user']['id'],'disabled':False},oc)
        account_client.request('admin-registration',{'enabled':False},oc)
        with self.assertRaisesRegex(ValueError,'关闭'):self.register('third')
        with self.assertRaisesRegex(ValueError,'管理员'):account_client.request('admin-disable',{'id':owner['user']['id'],'disabled':True},oc)
    def test_two_clients_same_identity_isolated_from_legacy(self):
        self.register('owner');guest,cookie=self.register('guest')
        _,cookie2=account_client.request('login',{'username':'guest','password':'password-12345'})
        one=account_client.resolve(self.base,cookie);two=account_client.resolve(self.base,cookie2)
        self.assertEqual(one['id'],two['id']);self.assertEqual(one['service'],two['service'])
        token=auth.CURRENT.set(one)
        try:self.assertEqual(auth.data_directory(self.base),self.base.resolve()/'services'/one['service']/str(one['id']))
        finally:auth.CURRENT.reset(token)
        account_client.request('admin-revoke',{'id':guest['user']['id']},self.register_admin_cookie())
        self.assertIsNone(account_client.resolve(self.base,cookie));self.assertIsNone(account_client.resolve(self.base,cookie2))
    def register_admin_cookie(self):return account_client.request('login',{'username':'owner','password':'password-12345'})[1]
    def test_no_chat_api_or_bad_origin(self):
        for path,origin in [('/api/chat',None),('/api/auth/state','https://other.example')]:
            req=urllib.request.Request(self.url+path,b'{}',headers={'X-Local-Token':self.server.token,**({'Origin':origin} if origin else {})})
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
            self.assertIn(e.exception.code,(403,404));e.exception.close()
        with self.assertRaisesRegex(ValueError,'先在本机'):account_server.create_server(self.base/'empty',0,'https://accounts.example.com')

if __name__=='__main__':unittest.main()
