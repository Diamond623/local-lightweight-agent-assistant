import json
import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
import weather
import search_plan
import conversation


class PlaceSearchTests(unittest.TestCase):
    def test_current_temperature_is_timestamped_and_rejects_stale_data(self):
        point=datetime.datetime(2026,11,9,12,15,tzinfo=datetime.timezone.utc)
        calls=[]
        def fetch(url):
            calls.append(url)
            if 'geocoding-api' in url:return {'results':[{'name':'成都','latitude':30.6,'longitude':104.1}]}
            return {'current':{'time':'2026-11-09T20:00','temperature_2m':12.5},'utc_offset_seconds':28800}
        result=weather.current_temperature('成都',fetch,lambda:point)
        self.assertEqual(result['temperature'],12.5)
        self.assertEqual(len(calls),2)
        with self.assertRaisesRegex(RuntimeError,'过旧'):
            weather.current_temperature('成都',fetch,lambda:point+datetime.timedelta(hours=3))
        self.assertEqual(weather.request_for('我这现在多少度',[]),{'city':None,'day':0})
        self.assertEqual(conversation.route('我这现在多少度',True),'search')

    def test_present_weather_smalltalk_requires_a_fresh_query(self):
        for q in ('今天好冷', 'Aurora 今天好冷', '现在真冷', '今天有点热'):
            self.assertEqual(weather.request_for(q, []), {'city': None, 'day': 0}, q)
            self.assertEqual(conversation.route(q, True), 'search', q)
        for q in ('今天脑子转不动了', '写一段今天好冷的文案', '天气模型的原理是什么'):
            self.assertIsNone(weather.request_for(q, []), q)
            self.assertEqual(conversation.route(q, True), 'local', q)

    def test_weather_today_refreshes_and_offline_cannot_repeat_old_temperature(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'DATA', Path(tmp)), patch.object(app, 'schedule_summary'):
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)', ('天气',app.now())).lastrowid
                db.execute("INSERT INTO preferences(key,value) VALUES('location_city',?)", (json.dumps('成都'),))
                db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)',
                           (sid,'assistant','昨日气温18℃',app.now()))
            queries=[]
            def search(query, places):
                queries.append((query,places))
                return [{'title':'成都今天天气','url':'https://example.org/weather',
                         'content':'今天成都气温 9℃，页面标注今日', 'read':True}]
            def generate(url,payload,timeout=90):
                if payload['max_tokens']==250:
                    return {'choices':[{'message':{'content':'{"places":["成都"],"topic":"天气"}'}}]}
                messages=payload['messages']
                self.assertNotIn('18℃',str(messages))
                self.assertIn('9℃',str(messages))
                return {'choices':[{'message':{'content':'本轮成都天气已重新查询。','reasoning_content':''},'finish_reason':'stop'}]}
            with patch.object(app,'model_info',return_value='gemma-4-12b'), patch.object(app,'check_external_models'), \
                 patch.object(app,'search_web',side_effect=search), patch.object(app,'request_json',side_effect=generate):
                result=app.chat(sid,'Aurora 今天好冷',None,'gemma',True,current_city='成都')
            self.assertEqual(result['route'],'search')
            self.assertEqual(queries,[('成都 今天天气 气温 降水',['成都'])])
            self.assertEqual(result['recalled'],[])
            with patch.object(app,'model_info',return_value=None), patch.object(app,'search_web') as forbidden:
                missing=app.chat(sid,'今天好冷',None,'gemma',True)
            forbidden.assert_not_called()
            self.assertIn('不会沿用之前保存的城市',missing['answer'])
            with patch.object(app,'model_info',return_value=None), patch.object(app,'search_web') as forbidden:
                offline=app.chat(sid,'今天好冷',None,'gemma',False)
            forbidden.assert_not_called()
            self.assertNotIn('18℃',offline['answer'])
            self.assertNotIn('9℃',offline['answer'])
            self.assertIn('没有联网查询',offline['answer'])
            self.assertIn('未开启联网',offline['search_note'])

    def test_weather_city_and_followup_routing(self):
        self.assertEqual(weather.request_for('珠海今天天气怎么样',[]),{'city':'珠海','day':0})
        self.assertEqual(weather.request_for('广州呢',[{'role':'user','content':'珠海今天天气怎么样'}]),{'city':'广州','day':0})
        self.assertIsNone(weather.request_for('天气模型的原理是什么',[]))

    def test_new_tasks_are_not_weather_followups(self):
        for previous in ([{'role':'assistant','content':'成都天气预报'},{'role':'user','content':'成都明天天气怎么样'}],[{'role':'assistant','content':weather.CLARIFY},{'role':'user','content':'明天天气怎么样'}]):
            for q in ['给你自己写一段推广词','写一段广告词','帮我解释递归','介绍一下你自己','总结这段文字']:
                self.assertIsNone(weather.request_for(q,previous),q)
        self.assertIsNone(weather.request_for('量子计算机',[{'role':'user','content':'成都明天天气怎么样'}]))
        self.assertEqual(weather.request_for('广州呢',[{'role':'user','content':'成都明天天气怎么样'}]),{'city':'广州','day':1})
        self.assertEqual(weather.request_for('雷克雅未克',[{'role':'assistant','content':weather.CLARIFY}]),{'city':'雷克雅未克','day':0})

    def test_planner_drops_old_topic_for_new_complete_question(self):
        previous=[{'role':'user','content':'成都明天天气怎么样'}]
        for q,places in [('量子计算机是什么',[]),('广州有哪些高校',['广州'])]:
            query,found=search_plan.extract(q,previous,lambda _:json.dumps({'places':places,'topic':'天气'}))
            self.assertEqual(query,q);self.assertEqual(found,places)

    def test_planner_rejects_old_or_invented_place(self):
        for invalid in ['成都','山东珠海']:
            with self.assertRaisesRegex(RuntimeError,'地点'):
                search_plan.extract('珠海天气',[],lambda _:json.dumps({'places':[invalid],'topic':''}))

    def test_model_repeating_current_topic_does_not_block_valid_search(self):
        query,places=search_plan.extract('珠海今天天气如何',[],lambda _:'{"places":["珠海"],"topic":"天气"}')
        self.assertEqual((query,places),('珠海今天天气如何',['珠海']))

    def test_general_location_and_topic_followup_without_city_catalogue(self):
        calls=[]
        def infer(messages):
            calls.append(messages)
            return '{"places":["雷克雅未克"],"topic":"旅游景点"}'
        query,places=search_plan.extract('雷克雅未克呢',[{'role':'assistant','content':'成都的旧数据'}, {'role':'user','content':'珠海有哪些旅游景点'}],infer)
        self.assertEqual(places,['雷克雅未克'])
        self.assertEqual(query,'雷克雅未克 旅游景点')
        self.assertNotIn('成都',str(calls))

    def test_consecutive_cities_fresh_search_and_isolated_prompt(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DATA',Path(tmp)),patch.object(app,'model_info',return_value='gemma-4-12b'),patch.object(app,'schedule_summary'):
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('地点测试',app.now())).lastrowid
                db.execute('INSERT INTO memories(content,created) VALUES(?,?)',('成都历史气温 20.1℃',app.now()))
                db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)',(sid,'user','成都天气',app.now()))
                db.execute('INSERT INTO messages(session_id,role,content,created) VALUES(?,?,?,?)',(sid,'assistant','成都 20.1℃',app.now()))
            queries=[];prompts=[]
            def search(query,places):
                place=places[0];queries.append((query,places))
                return [{'title':place+'天气预报','url':'https://example.org/'+place,'snippet':'','content':place+' 当前页面天气信息 25℃','read':True}]
            def generate(url,payload,timeout=4):
                if payload['max_tokens']==250:
                    current=json.loads(payload['messages'][1]['content'])['current_question']
                    place='珠海' if '珠海' in current else '广州'
                    text=json.dumps({'places':[place],'topic':''})
                else:
                    prompts.append(payload['messages'])
                    text='已根据本轮网页作答 [1]'
                return {'choices':[{'message':{'content':text},'finish_reason':'stop'}]}
            with patch.object(app,'search_web',side_effect=search),patch.object(app,'request_json',side_effect=generate):
                a=app.chat(sid,'珠海今天天气如何',None,'gemma',True)
                b=app.chat(sid,'广州呢',None,'gemma',True)
            self.assertEqual([q[1] for q in queries],[['珠海'],['广州']])
            self.assertEqual([q[0] for q in queries],['珠海 今天天气 气温 降水','广州 今天天气 气温 降水'])
            self.assertNotIn('珠海',queries[1][0])
            for prompt in prompts:
                self.assertNotIn('成都',str(prompt));self.assertNotIn('20.1',str(prompt))
                self.assertEqual([m['role'] for m in prompt],['system','user'])
            self.assertNotIn('珠海',str(prompts[1]))
            self.assertEqual(a['recalled'],[]);self.assertEqual(b['recalled'],[])
            self.assertIn('广州',b['search_note'])
            with app.database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],6)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
