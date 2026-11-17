import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import app
import conversation
import memory_engine


class ConversationTests(unittest.TestCase):
    def test_permission_does_not_turn_every_message_into_a_search(self):
        for q in ['附近有什么好吃的','我这里有什么活动','当地路况怎么样']:
            self.assertEqual(conversation.route(q,True),'search',q)
            self.assertEqual(conversation.route(q,False),'local',q)
        for q in ['以后你叫我老大','我让你以后叫我老大','早上好Aurora，你在做什么',
                  '你好蠢','今天脑子转不动了','给自己编一段跟星际有关的推广词',
                  '为什么极光会发光','什么是天气预报','新闻稿怎么写','联网之后你怎么变笨了','允许联网不是一定要联网']:
            self.assertEqual(conversation.route(q,True),'local',q)
        for q in ['广州今天天气','今天有什么新闻','查查Gemma的资料','搜索极光的形成原理']:
            self.assertEqual(conversation.route(q,True),'search',q)
            self.assertEqual(conversation.route(q,False),'local',q)
        self.assertEqual(conversation.route('不用联网搜索，解释天气预报的原理',True),'local')

    def test_routing_keeps_private_and_creative_tasks_local(self):
        for q in ['你好呀','你好啊！','晚上好','谢谢啦','你认识我吗','你还认识我吗','hello','Hi!']:
            self.assertEqual(conversation.route(q,True),'local',q)
        for q in ['你知道我是谁吗','你对我了解多少','我喜欢什么','我的学校是哪所','我叫什么名字','你是哪个公司开发的','你现在是什么模型','写一篇关于春天的短文','解释归结法','写一份新闻稿介绍校园活动','1+1']:
            self.assertEqual(conversation.route(q,True),'local',q)
        self.assertEqual(conversation.route('解释最新发布的模型',True),'search')
        self.assertEqual(conversation.route('以后回答自然一点',True),'local')
        self.assertEqual(conversation.route('回答详细点，搜索广州今天的天气',True),'search')
        self.assertEqual(conversation.route('广州有哪些景点',True),'search')
        self.assertEqual(conversation.route('概括文档',True,True),'attachment')
        self.assertEqual(conversation.route('联网核实这个结论',True,True),'search')
        self.assertEqual(conversation.route('为什么',True),'transform')
        self.assertEqual(conversation.route('举个例子',True),'transform')

    def test_recipient_prefixed_writing_tasks_do_not_search(self):
        for q in ['给你自己写一段推广词','你给自己写段广告','帮Aurora写一段介绍文案','给小李写封信','请你为我们创作一段广告词','能不能给我写个故事','写一段天气主题的广告词','把上一段改成广告词']:
            self.assertEqual(conversation.route(q,True),'local',q)
        self.assertEqual(conversation.route('先搜索 Aurora 的最新资料，再写一段推广词',True),'search')

    def test_memory_questions_are_not_save_commands(self):
        self.assertEqual(conversation.remember_command('请记住：我叫星河'),'我叫星河')
        for q in ['你记住了吗','记住了吗','记住我叫什么名字了吗？','不要记住这件事']:
            self.assertIsNone(conversation.remember_command(q),q)

    def test_long_answer_keeps_recent_question_and_continuation_tail(self):
        previous=[{'role':'assistant','content':'开头'+('长回答'*2000)+'末尾的未完步骤'}, {'role':'user','content':'教我归结法'}]
        history=conversation.history(previous,2200,'继续')
        self.assertEqual([r['role'] for r in history],['user','assistant'])
        self.assertIn('末尾的未完步骤',history[-1]['content'])
        self.assertLessEqual(sum(len(r['content']) for r in history),2200)

    def test_source_followups_preserve_place_but_new_place_is_not_replaced(self):
        meta={'query':'珠海 明天天气 气温 降水','places':['珠海'],'weather':True,'day':1}
        previous=[{'role':'assistant','content':'珠海预报','sources':json.dumps([{'title':'珠海天气','_aurora':meta}])},
                  {'role':'user','content':'明天呢','sources':'[]'}]
        self.assertEqual(conversation.followup_question('后天呢',previous),'珠海 后天天气')
        self.assertEqual(conversation.followup_question('会下雨吗',previous),'珠海 明天天气 降水概率 要带伞吗')
        self.assertEqual(conversation.followup_question('广州呢',previous),'广州呢')
        self.assertEqual(conversation.followup_question('那里有什么景点',previous),'珠海 那里有什么景点')
        self.assertIn('明天天气',conversation.planning_history(previous)[1]['content'])
        self.assertEqual(previous[1]['content'],'明天呢')

    def test_web_keeps_answer_preferences_without_old_weather_facts(self):
        rows=[{'kind':'note','content':'叫我星河，用中文简短回答，成都昨天气温20℃'},
              {'kind':'message','content':'旧网页广州21℃'}]
        chosen=conversation.web_preferences(rows)
        self.assertEqual(len(chosen),1)
        self.assertIn('星河',chosen[0]['content']);self.assertNotIn('成都',chosen[0]['content'])

    def test_old_version_weather_history_restores_only_matching_literal_place(self):
        previous=[{'role':'assistant','content':'旧数据不应复用','sources':json.dumps([{'title':'广州天气预报','url':'https://example.org/gz'}])},
                  {'role':'user','content':'广州呢','sources':'[]'}]
        self.assertEqual(conversation.followup_question('明天呢',previous),'广州 明天天气')
        self.assertEqual(conversation.followup_question('珠海呢',previous),'珠海呢')
        previous[1]['content']='成都呢'
        self.assertEqual(conversation.followup_question('明天呢',previous),'明天呢')


class ChatImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(app,'DATA',Path(self.temp.name));self.patch.start()
        with app.database() as db:self.sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('新对话',app.now())).lastrowid
        self.summary=patch.object(app,'schedule_summary');self.summary.start()
    def tearDown(self):
        self.summary.stop();self.patch.stop();self.temp.cleanup()
    def test_explicit_remember_works_without_model_and_avoids_duplicates(self):
        with patch.object(app,'model_info',return_value=None),patch.object(app,'search_web') as search:
            app.chat(self.sid,'记住：我叫星河',None,'gemma',True)
            app.chat(self.sid,'记住：我叫星河',None,'text',False)
            search.assert_not_called()
        with app.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],1)
            self.assertTrue(any('星河' in r['content'] for r in memory_engine.recall(db,'你知道我是谁吗',999)))
    def test_name_is_recalled_even_after_many_new_notes_and_assistant_guess_is_excluded(self):
        with app.database() as db:
            for text in ['我叫星河']+[f'周末任务编号{i}' for i in range(12)]:
                cur=db.execute('INSERT INTO memories(content,created) VALUES(?,?)',(text,app.now()));memory_engine.index(db,'note',cur.lastrowid,None,text)
            cur=db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)',(self.sid,'assistant','你的名字是误报的名字',app.now()))
            memory_engine.index(db,'message',cur.lastrowid,self.sid,'你的名字是误报的名字')
            result=memory_engine.recall(db,'你知道我是谁吗',self.sid)
            self.assertTrue(any('星河' in r['content'] for r in result))
            self.assertFalse(any('误报' in r['content'] for r in result))
    def test_old_response_preference_remains_available_for_web_answers(self):
        with app.database() as db:
            for text in ['用中文简短回答']+[f'周末任务编号{i}' for i in range(12)]:
                cur=db.execute('INSERT INTO memories(content,created) VALUES(?,?)',(text,app.now()));memory_engine.index(db,'note',cur.lastrowid,None,text)
            notes=conversation.web_preferences(memory_engine.recall(db,'广州天气',self.sid))
        self.assertTrue(any('简短' in row['content'] for row in notes))
    def test_online_identity_and_attachment_do_not_call_search(self):
        response={'choices':[{'message':{'content':'本地回答'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response),patch.object(app,'search_web') as search:
            app.chat(self.sid,'你知道我是谁吗',None,'gemma',True)
            app.chat(self.sid,'概括这份文件',None,'gemma',True,{'name':'notes.txt','text':'课程内容归结法','images':[]})
            search.assert_not_called()
    def test_nickname_after_web_answer_does_not_search_or_reuse_web_sources(self):
        sources=[{'title':'成都天气','url':'https://example.org/weather',
                  '_aurora':{'query':'成都今天天气','places':['成都'],'weather':True,'day':0}}]
        with app.database() as db:
            db.execute('INSERT INTO messages(session_id,role,content,sources,created) VALUES(?,?,?,?,?)',
                       (self.sid,'assistant','成都天气',json.dumps(sources),app.now()))
        response={'choices':[{'message':{'content':'好的，老大。'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response),patch.object(app,'search_web') as search,patch.object(app.search_plan,'extract') as planner:
            result=app.chat(self.sid,'以后你叫我老大',None,'gemma',True)
        search.assert_not_called();planner.assert_not_called()
        self.assertEqual(result['sources'],[])
        self.assertEqual(result['route'],'local')

    def test_general_web_answer_is_not_told_to_report_location_and_date(self):
        source={'title':'极光的形成','url':'https://example.org/aurora','content':'太阳风中的带电粒子与高层大气相互作用形成极光。'}
        response={'choices':[{'message':{'content':'极光的解释 [1]'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response) as generate,patch.object(app,'search_web',return_value=[source]),patch.object(app.search_plan,'extract',return_value=('搜索极光的形成原理',[])):
            app.chat(self.sid,'搜索极光的形成原理',None,'gemma',True)
        prompt=generate.call_args.args[1]['messages'][-1]['content']
        self.assertNotIn('回答当前地点和日期',prompt)
        self.assertIn('只回答本轮用户提出的问题',prompt)
    def test_writing_after_weather_never_invokes_search_planner(self):
        sources=[{'title':'成都天气预报','url':'https://example.org/weather','_aurora':{'query':'成都 明天天气','places':['成都'],'weather':True,'day':1}}]
        with app.database() as db:
            db.execute('INSERT INTO messages(session_id,role,content,sources,created) VALUES(?,?,?,?,?)',(self.sid,'user','成都明天天气怎么样','[]',app.now()))
            db.execute('INSERT INTO messages(session_id,role,content,sources,created) VALUES(?,?,?,?,?)',(self.sid,'assistant','成都明天阴天',json.dumps(sources),app.now()))
        response={'choices':[{'message':{'content':'Aurora，让灵感有下文。'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response) as generate,patch.object(app,'search_web') as search,patch.object(app.search_plan,'extract') as planner:
            result=app.chat(self.sid,'给你自己写一段推广词',None,'gemma',True)
            search.assert_not_called();planner.assert_not_called()
        self.assertEqual(result['route'],'local');self.assertEqual(result['sources'],[])
        self.assertEqual(result['answer'],'Aurora，让灵感有下文。')
        self.assertEqual(generate.call_args.args[1]['messages'][-1]['content'],'给你自己写一段推广词')

    def test_rewrite_reuses_sources_instead_of_new_search(self):
        sources=[{'title':'广州天气','url':'https://example.org/gz','content':'广州20℃'}]
        with app.database() as db:
            db.execute('INSERT INTO messages(session_id,role,content,sources,created) VALUES(?,?,?,?,?)',(self.sid,'user','广州天气','[]',app.now()))
            db.execute('INSERT INTO messages(session_id,role,content,sources,created) VALUES(?,?,?,?,?)',(self.sid,'assistant','广州20℃',json.dumps(sources),app.now()))
        response={'choices':[{'message':{'content':'简短回答 [1]'},'finish_reason':'stop'}]}
        with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response) as generate,patch.object(app,'search_web') as search:
            result=app.chat(self.sid,'简短一点',None,'gemma',True)
            search.assert_not_called();self.assertEqual(result['sources'][0]['url'],sources[0]['url'])
            self.assertIn('广州20℃',str(generate.call_args.args[1]['messages']))


class IdleSummaryTests(unittest.TestCase):
    def test_auto_summary_does_not_take_chat_lock_immediately(self):
        done=threading.Event()
        with patch.object(app,'SUMMARY_IDLE_SECONDS',.15),patch.object(app,'LAST_INTERACTION',time.monotonic()),patch.object(app,'summarize_session',side_effect=lambda *args:(done.set() or False)) as summary:
            self.assertTrue(app.schedule_summary(1,'gemma'))
            acquired=app.CHAT_LOCK.acquire(blocking=False)
            self.assertTrue(acquired)
            if acquired:app.CHAT_LOCK.release()
            self.assertFalse(summary.called)
            self.assertTrue(done.wait(1))
            self.assertTrue(app.SUMMARY_LOCK.acquire(timeout=1))
            app.SUMMARY_LOCK.release()

if __name__=='__main__':unittest.main()
