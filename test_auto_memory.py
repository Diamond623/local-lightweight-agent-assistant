import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import app
import auto_memory as am
import memory_engine as me
import memory_worker

class AutoMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(app,'DATA',Path(self.tmp.name));self.patch.start()
        self.ctx=app.auth.CURRENT.set(None)
        with app.database() as db:self.sid=db.execute("INSERT INTO sessions(title,created) VALUES('测试','now')").lastrowid
    def tearDown(self):
        app.auth.CURRENT.reset(self.ctx);self.patch.stop();self.tmp.cleanup()
    def message(self,db,text,role='user',attachment='{}'):
        return db.execute('INSERT INTO messages(session_id,role,content,created,attachment) VALUES(?,?,?,?,?)',(self.sid,role,text,app.now(),attachment)).lastrowid
    def capture(self,db,text):
        mid=self.message(db,text);return mid,am.apply(db,mid,text,am.rules(text))
    def test_unrelated_recent_memories_and_summary_are_not_injected(self):
        with app.database() as db:
            for text in ('我在学习C语言指针','我喜欢杭州西湖旅游','昨天北京下雨','我家猫喜欢罐头'):
                mid=db.execute('INSERT INTO memories(content,created) VALUES(?,?)',(text,app.now())).lastrowid;me.index(db,'note',mid,None,text)
            db.execute('INSERT INTO summaries(session_id,first_id,last_id,content,created) VALUES(?,1,2,?,?)',(self.sid,'上海天气与旅行',app.now()))
            rows=me.recall(db,'C语言指针怎么用',self.sid)
            self.assertTrue(any('指针' in x['content'] for x in rows));self.assertNotIn('西湖',str(rows));self.assertNotIn('罐头',str(rows));self.assertNotIn('旅行',str(rows))
    def test_explicit_user_fact_update_keeps_source_and_revision(self):
        with app.database() as db:
            first,changes=self.capture(db,'我叫小林。');self.assertEqual(len(changes),1)
            second,changes=self.capture(db,'现在我叫小李。');self.assertEqual(len(changes),1)
            rows=db.execute('SELECT * FROM memories WHERE active=1').fetchall();self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['content'],'我叫小李');self.assertEqual(rows[0]['source_message_id'],second)
            self.assertIn('小林',db.execute("SELECT previous_content FROM memory_events WHERE action='update'").fetchone()[0])
            am.apply(db,first,'我叫小林。',am.rules('我叫小林。'));self.assertEqual(db.execute('SELECT content FROM memories WHERE active=1').fetchone()[0],'我叫小李')
    def test_not_questions_quotes_sensitive_or_third_party(self):
        for text in ['我叫什么名字','我喜欢什么','我叫小林吗？','假设我叫小林','他说我叫小林','翻译：我叫小林','我叫“小林”','我的密码是12345','我今天心情不好','明天天气怎么样']:
            self.assertFalse(am.eligible(text),text)
        with app.database() as db:
            text='我叫小林。';mid=self.message(db,text,role='assistant')
            self.assertEqual(am.apply(db,mid,text,am.rules(text)),[])
            mid=self.message(db,text,attachment='{"name":"document"}')
            self.assertEqual(am.apply(db,mid,text,am.rules(text)),[])
    def test_model_must_quote_literal_self_report(self):
        text='我希望研究法律推理。'
        good={'key':'goal.research','value':'法律推理','evidence':'我希望研究法律推理','confidence':.99}
        self.assertTrue(am.valid(text,good))
        for delta in ({'value':'机器人'},{'evidence':'我已经发表法律论文'},{'confidence':.5},{'key':'password'}):self.assertFalse(am.valid(text,{**good,**delta}))
        self.assertEqual(am.extract(text,lambda _:json.dumps({'items':[good]})),[good])
        self.assertEqual(am.extract(text,lambda _:'not JSON'),[])
    def test_disabled_midflight_and_old_source_cannot_reappear(self):
        with app.database() as db:
            text='我叫小林。';mid=self.message(db,text)
            am.settings(db,False);am.settings(db,True)
            self.assertEqual(am.apply(db,mid,text,am.rules(text)),[])
            new,changes=self.capture(db,text);self.assertTrue(changes)
            am.forget(db,changes[0]['id']);self.assertEqual(am.apply(db,new,text,am.rules(text)),[])
            self.assertFalse(any('小林' in r['content'] for r in me.recall(db,'我叫什么名字',self.sid)))
            _,changes=self.capture(db,text);self.assertTrue(changes,'A new explicit restatement may be remembered again')
    def test_manual_precedence_and_explicit_correction(self):
        with app.database() as db:
            note=am.manual(db,'我叫小林')
            _,changes=self.capture(db,'我叫小李。');self.assertEqual(changes,[])
            _,changes=self.capture(db,'更正，现在我叫小李。');self.assertTrue(changes)
            self.assertEqual(db.execute('SELECT origin FROM memories WHERE id=?',(note,)).fetchone()[0],'manual')
    def test_manual_edit_blocks_older_inflight_correction(self):
        with app.database() as db:
            text='更正，现在我叫小林。';mid,changes=self.capture(db,text)
            am.manual(db,'我叫小李',changes[0]['id'])
            self.assertEqual(am.apply(db,mid,text,am.rules(text)),[])
            self.assertEqual(db.execute('SELECT content FROM memories WHERE active=1').fetchone()[0],'我叫小李')

    def test_same_progress_evidence_does_not_duplicate_across_subjects(self):
        with app.database() as db:
            text='我已经学完C语言数组，接下来准备学习指针。';mid=self.message(db,text)
            candidates=[{'key':'learning.progress','subject':subject,'value':'数组','evidence':text,'confidence':1} for subject in ('C语言','指针')]
            self.assertEqual(len(am.apply(db,mid,text,candidates)),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories WHERE active=1').fetchone()[0],1)

    def test_progress_subjects_are_kept_separately(self):
        with app.database() as db:
            for subject,text in [('C语言','我已经学完C语言数组。'),('知识表示','我已经学完知识表示归结法。')]:
                c={'key':'learning.progress','value':text[:-1],'evidence':text[:-1],'confidence':1,'subject':subject}
                mid=self.message(db,text);self.assertTrue(am.apply(db,mid,text,[c]))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memories WHERE memory_key LIKE 'learning.progress:%' AND active=1").fetchone()[0],2)
    def test_restoring_old_memory_explicitly_supersedes_current_slot(self):
        with app.database() as db:
            _,changes=self.capture(db,'我叫小林。');old=changes[0]['id']
            current=am.manual(db,'我叫小李')
            self.assertEqual(db.execute('SELECT active FROM memories WHERE id=?',(old,)).fetchone()[0],0)
            row=db.execute('SELECT content FROM memories WHERE id=?',(old,)).fetchone()
            am.manual(db,row['content'],old)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories WHERE active=1').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT active FROM memories WHERE id=?',(current,)).fetchone()[0],0)

    def test_prune_archives_only_automatic_notes_and_preserves_history(self):
        with app.database() as db,patch.object(am,'MAX_ACTIVE',2):
            manual=am.manual(db,'手动保留的记录')
            for text in ['我叫小林。','我的专业是人工智能。','我的学校是电子科技大学。']:self.capture(db,text)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memories WHERE origin='auto' AND active=1").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT active FROM memories WHERE id=?',(manual,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],3)
            db.execute("UPDATE memories SET expires=1 WHERE origin='auto'");am.prune(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memories WHERE origin='auto' AND active=1").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories WHERE active=0').fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_fts WHERE kind='note'").fetchone()[0],1)
    def test_new_chat_can_cancel_background_extraction(self):
        import threading
        entered=threading.Event()
        text='我已经学完C语言数组。'
        with app.database() as db:mid=self.message(db,text)
        def blocked(url,payload,cancelled,timeout=30):
            entered.set()
            for _ in range(500):
                if cancelled():raise app.generation.Cancelled('让出模型')
                time.sleep(.005)
            self.fail('Background request was not cancelled')
        with patch.object(app,'model_info',return_value='test'),patch.object(app.generation,'request_json',side_effect=blocked):
            memory_worker.schedule(mid,'text');self.assertTrue(entered.wait(2))
            self.assertTrue(memory_worker.preempt())
            self.assertTrue(app.CHAT_LOCK.acquire(timeout=2));app.CHAT_LOCK.release()
            for _ in range(200):
                with memory_worker._LOCK:pending=bool(memory_worker._QUEUED)
                if not pending:break
                time.sleep(.005)
            self.assertFalse(pending)

    def test_worker_carries_account_context(self):
        user={'id':2,'owner':0,'display_name':'测试'};app.auth.CURRENT.set(user)
        text='我已经学完C语言数组。'
        with app.database() as db:mid=self.message(db,text)
        candidate={'key':'learning.progress','subject':'C语言','value':'数组','evidence':text[:-1],'confidence':1}
        response={'choices':[{'message':{'content':json.dumps({'items':[candidate]})},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app.generation,'request_json',return_value=response):
            memory_worker.schedule(mid,'text')
            for _ in range(300):
                with memory_worker._LOCK:pending=bool(memory_worker._QUEUED)
                if not pending:break
                time.sleep(.01)
            self.assertFalse(pending)
        with app.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM memories WHERE active=1').fetchone()[0],1)
        app.auth.CURRENT.set(None)
        with app.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],0)

if __name__=='__main__':unittest.main()
