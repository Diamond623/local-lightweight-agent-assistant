import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import app
import auth
import data_paths


class StatePersistenceTests(unittest.TestCase):
    def test_storage_survives_installation_move_and_preserves_local_database(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            desktop = home / 'Desktop/Aurora Memory'
            desktop.mkdir(parents=True)
            (desktop / 'accounts.sqlite3').write_bytes(b'existing')
            old, new = home / 'old', home / 'new'
            old.mkdir(); new.mkdir()
            (old / 'data').symlink_to(desktop)
            self.assertEqual(data_paths.data_root(old, home), desktop.resolve())
            self.assertEqual(data_paths.data_root(new, home), desktop)
            isolated = home / 'other-home'
            self.assertEqual(data_paths.data_root(new, isolated), isolated / 'Library/Application Support/Aurora')
            local = new / 'data'; local.mkdir()
            (local / 'memory.sqlite3').write_bytes(b'local history')
            self.assertEqual(data_paths.data_root(new, isolated), local)

    def test_account_and_history_survive_restart_with_account_isolation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'DATA', Path(temp)):
            context = auth.CURRENT.set(None)
            def serve():
                server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return server, thread
            def post(server, action, obj=None, cookie=''):
                req = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/api/' + action,
                    json.dumps(obj or {}).encode(), headers={'Content-Type': 'application/json',
                    'X-Local-Token': app.TOKEN, 'Cookie': cookie})
                with urllib.request.urlopen(req) as response:
                    return json.load(response), response.headers.get('Set-Cookie')
            try:
                server, thread = serve()
                try:
                    owner, cookie = post(server, 'auth/register', {'username': 'owner', 'display_name': '保留称呼', 'password': 'password-12345', 'remember': True})
                    sid = post(server, 'new', cookie=cookie)[0]['id']
                    post(server, 'new', cookie=cookie)
                    post(server, 'active-session', {'session': sid}, cookie)
                    _, guest = post(server, 'auth/register', {'username': 'guest', 'display_name': '访客', 'password': 'password-12345'})
                    self.assertIsNone(post(server, 'active-session', cookie=guest)[0]['session'])
                    auth.CURRENT.set(owner['user'])
                    with app.database() as db:
                        db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)', (sid, 'user', '保留内容', app.now()))
                    auth.CURRENT.set(None)
                finally:
                    server.shutdown(); server.server_close(); thread.join()
                server, thread = serve()
                try:
                    self.assertEqual(post(server, 'auth/state', cookie=cookie)[0]['user']['display_name'], '保留称呼')
                    self.assertEqual(post(server, 'active-session', cookie=cookie)[0]['session'], sid)
                    self.assertEqual(len(post(server, 'sessions', cookie=cookie)[0]), 2)
                    self.assertEqual(post(server, 'messages', {'session': sid}, cookie)[0][0]['content'], '保留内容')
                    self.assertIsNone(post(server, 'active-session', cookie=guest)[0]['session'])
                    req = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/admin', headers={'Cookie': cookie})
                    with urllib.request.urlopen(req) as response:
                        self.assertIn('id="backtochat" href="/"', response.read().decode())
                finally:
                    server.shutdown(); server.server_close(); thread.join()
            finally:
                auth.CURRENT.reset(context)

    def test_archive_migration_preserves_messages_and_persists(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'DATA', Path(temp)):
            context=auth.CURRENT.set(None)
            try:
                with sqlite3.connect(str(Path(temp)/'memory.sqlite3')) as db:
                    db.execute('CREATE TABLE sessions(id INTEGER PRIMARY KEY,title TEXT,created TEXT)')
                    db.execute("INSERT INTO sessions VALUES(7,'旧对话','now')")
                with app.database() as db:
                    self.assertEqual(db.execute('SELECT archived FROM sessions WHERE id=7').fetchone()[0],0)
                    db.execute("INSERT INTO messages(session_id,role,content,created) VALUES(7,'user','保留原文','now')")
                    db.execute('UPDATE sessions SET archived=1 WHERE id=7')
                with app.database() as db:
                    self.assertEqual(db.execute('SELECT archived FROM sessions WHERE id=7').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT content FROM messages WHERE session_id=7').fetchone()[0],'保留原文')
            finally:auth.CURRENT.reset(context)

    def test_image_only_does_not_fabricate_a_user_question(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'DATA', Path(temp)):
            context = auth.CURRENT.set(None)
            captured = []
            try:
                with app.database() as db:
                    sid = db.execute("INSERT INTO sessions(title,created) VALUES('新对话','now')").lastrowid
                    db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)', (sid, 'user', '核对下一张截图', 'now'))
                def answer(url, payload, **kwargs):
                    captured.append(payload)
                    return {'choices': [{'message': {'content': '已收到，继续核对。'}, 'finish_reason': 'stop'}]}
                with patch.object(app, 'start_model'), patch.object(app, 'model_info', return_value='fixture'), patch.object(app, 'request_json', side_effect=answer), patch.object(app, 'schedule_summary'):
                    app.chat(sid, '', 'data:image/jpeg;base64,aGVsbG8=', 'gemma', False)
                with app.database() as db:
                    contents = [row[0] for row in db.execute("SELECT content FROM messages WHERE role='user' ORDER BY id")]
                self.assertEqual(contents, ['核对下一张截图', ''])
                self.assertIn('核对下一张截图', str(captured[-1]['messages']))
                self.assertNotIn('请描述这张图片的主要内容', str(captured))
            finally:
                auth.CURRENT.reset(context)


if __name__ == '__main__':
    unittest.main()
