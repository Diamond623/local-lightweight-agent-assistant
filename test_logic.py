import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import memory_engine


class LocalAssistantTests(unittest.TestCase):
    def test_existing_database_upgrades_without_losing_conversation_or_notes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)):
            connection=sqlite3.connect(Path(tmp)/'memory.sqlite3')
            connection.executescript("""
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id INTEGER,role TEXT,content TEXT,image TEXT,sources TEXT,created TEXT);
                CREATE TABLE memories(id INTEGER PRIMARY KEY,content TEXT,created TEXT);
                INSERT INTO messages VALUES(1,1,'user','旧聊天保留','','[]','2026-10-01');
                INSERT INTO memories VALUES(1,'旧记忆保留','2026-10-01');
            """)
            connection.close()
            for _ in range(2):
                with app.database() as db:
                    self.assertEqual(db.execute('SELECT content FROM messages').fetchone()[0],'旧聊天保留')
                    self.assertEqual(db.execute('SELECT content FROM memories').fetchone()[0],'旧记忆保留')
                    self.assertEqual(db.execute('SELECT attachment FROM messages').fetchone()[0],'{}')
            self.assertTrue((Path(tmp)/'memory-before-retrieval-v1.sqlite3').is_file())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(app, 'DATA', Path(self.temp.name))
        self.patch.start()
        with app.database() as db:
            self.session = db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',
                                      ('新对话', app.now())).lastrowid

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_memory_persistence_and_forgetting_change_prompt(self):
        with app.database() as db:
            app.auto_memory.manual(db,'称呼我小林')
        calls = []

        def inference(url, payload=None, timeout=4):
            calls.append(payload)
            return {'choices': [{'message': {'content': '小林你好'}, 'finish_reason': 'stop'}]}

        with patch.object(app, 'start_model'), patch.object(app, 'model_info', return_value='test'), patch.object(app, 'request_json', side_effect=inference):
            app.chat(self.session, '你好', None, 'text', False)
            self.assertIn('称呼我小林', calls[-1]['messages'][0]['content'])
            with app.database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)
                db.execute('DELETE FROM memories')
            app.chat(self.session, '再问一次', None, 'text', False)
            self.assertNotIn('称呼我小林', calls[-1]['messages'][0]['content'])

    def test_chat_starts_selected_model_and_waits_until_ready(self):
        result={'choices':[{'message':{'content':'自动启动后完成回复'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',side_effect=[None,'test']), patch.object(app,'start_model') as start, patch.object(app.time,'sleep'), patch.object(app,'request_json',return_value=result), patch.dict(app.PROCESSES,{},clear=True):
            answer=app.chat(self.session,'解释一下递归',None,'text',False)
            start.assert_called_once_with('text')
            self.assertIn('自动启动后',answer['answer'])

    def test_start_failure_keeps_chat_history_unchanged(self):
        with patch.object(app,'model_info',return_value=None), patch.object(app,'start_model',side_effect=RuntimeError('缺少模型文件')):
            with self.assertRaisesRegex(RuntimeError,'缺少模型文件'):app.chat(self.session,'解释一下递归',None,'text',False)
        with app.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_text_model_rejects_images(self):
        with self.assertRaisesRegex(ValueError, '看图模型'):
            app.chat(self.session, '看图', 'data:image/png;base64,aGVsbG8=', 'text', False)

    def test_search_failure_does_not_save_partial_conversation(self):
        with patch.object(app, 'start_model'), patch.object(app, 'model_info', return_value='test'), patch.object(app, 'search_web', side_effect=RuntimeError('offline')), patch.object(app.search_plan, 'extract', return_value=('今天有什么新闻', [])):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                app.chat(self.session, '今天有什么新闻', None, 'text', True)
        with app.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_api_requires_token_and_supports_delete(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        _,cookie=app.auth.api(app.DATA,'register',{'username':'tester','display_name':'测试','password':'test-password-123'},None,'')
        def post(path, data, token=app.TOKEN):
            req = urllib.request.Request(base + '/api/' + path, json.dumps(data).encode(),
                                         headers={'X-Local-Token': token,'Cookie':cookie})
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                post('memories', {}, token='wrong')
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            post('remember', {'text': '喜欢中文'})
            post('rename-session', {'session': self.session, 'title': '学习与研究'})
            self.assertEqual(post('sessions', {})[0]['title'], '学习与研究')
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                post('rename-session', {'session': self.session, 'title': '  '})
            invalid.exception.close()
            self.assertEqual(post('sessions', {})[0]['title'], '学习与研究')
            rows = post('memories', {})
            self.assertEqual(rows[0]['content'], '喜欢中文')
            post('forget', {'id': rows[0]['id']})
            self.assertEqual(post('memories', {}), [])
            post('remember', {'text': '考研目标是数学'})
            note = post('memories', {})[0]
            post('update-memory', {'id': note['id'], 'text': '考研目标改为人工智能'})
            self.assertEqual(post('memories', {})[0]['content'], '考研目标改为人工智能')
            with app.database() as db:
                indexed = db.execute("SELECT content FROM memory_fts WHERE kind='note'").fetchall()
                self.assertEqual([r['content'] for r in indexed], ['考研目标改为人工智能'])
            post('delete-session', {'session': self.session})
            self.assertEqual(post('sessions', {}), [])
        finally:
            server.shutdown()
            server.server_close()

    def test_old_note_beyond_thirty_is_retrieved(self):
        with app.database() as db:
            for i in range(65):
                content = '我正在学习归结证明和合一算法' if i == 0 else f'最近记录：周末任务编号{i}'
                cur = db.execute('INSERT INTO memories(content,created) VALUES(?,?)', (content,app.now()))
                memory_engine.index(db,'note',cur.lastrowid,None,content)
            recalled = memory_engine.recall(db,'归结证明学到哪里', self.session)
            self.assertTrue(any('合一算法' in r['content'] for r in recalled))
            db.execute('DELETE FROM memories WHERE id=1')
            recalled = memory_engine.recall(db,'归结证明学到哪里', self.session)
            self.assertFalse(any('合一算法' in r['content'] for r in recalled))

    def test_history_across_sessions_and_delete_cleans_index(self):
        with app.database() as db:
            cur=db.execute('INSERT INTO messages(session_id,role,content,image,sources,created) VALUES(?,?,?,?,?,?)',
                           (self.session,'user','C语言已经学完数组，下一步学习指针','','[]',app.now()))
            memory_engine.index(db,'message',cur.lastrowid,self.session,'C语言已经学完数组，下一步学习指针')
            sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('第二个对话',app.now())).lastrowid
            self.assertTrue(any('指针' in r['content'] for r in memory_engine.recall(db,'C语言进度',sid)))
            db.execute('DELETE FROM messages WHERE session_id=?',(self.session,))
            db.execute('DELETE FROM sessions WHERE id=?',(self.session,))
            self.assertFalse(any('指针' in r['content'] for r in memory_engine.recall(db,'C语言进度',sid)))

    def seed_long_history(self):
        with app.database() as db:
            for i in range(16):
                content = f'第{i}条：学习指针，完成了数组练习'
                cur = db.execute('INSERT INTO messages(session_id,role,content,image,sources,created) VALUES(?,?,?,?,?,?)',
                                 (self.session,'user' if i%2==0 else 'assistant',content,'','[]',app.now()))
                memory_engine.index(db,'message',cur.lastrowid,self.session,content)

    def test_summary_checkpoint_retains_original_and_is_shared(self):
        self.seed_long_history()
        response={'choices':[{'message':{'content':'用户自述已完成数组练习，计划学习指针。'}}]}
        with patch.object(app,'model_info',return_value='test'), patch.object(app,'request_json',return_value=response):
            self.assertTrue(app.summarize_session(self.session,'text'))
            self.assertFalse(app.summarize_session(self.session,'text'))
        with app.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],16)
            row=db.execute('SELECT * FROM summaries').fetchone()
            self.assertEqual((row['first_id'],row['last_id']),(1,12))
            self.assertTrue(any(r['kind']=='summary' for r in memory_engine.recall(db,'学习指针',999)))
            db.execute('DELETE FROM sessions WHERE id=?',(self.session,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM summaries').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_fts').fetchone()[0],0)

    def test_failed_summary_does_not_advance_checkpoint(self):
        self.seed_long_history()
        with patch.object(app,'model_info',return_value='test'), patch.object(app,'request_json',side_effect=RuntimeError('模型断开')):
            with self.assertRaisesRegex(RuntimeError,'模型断开'):
                app.summarize_session(self.session,'text')
        with app.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],16)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM summaries').fetchone()[0],0)

    def test_upgrade_backfills_legacy_records(self):
        with app.database() as db:
            db.execute('INSERT INTO memories(content,created) VALUES(?,?)',('研究法律推理',app.now()))
            db.execute("DELETE FROM memory_meta WHERE key='index_v1'")
        with app.database() as db:
            self.assertTrue(any('法律推理' in r['content'] for r in memory_engine.recall(db,'法律推理',self.session)))


if __name__ == '__main__':
    unittest.main()
