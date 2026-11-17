import unittest
from unittest.mock import patch
import urllib.error
import tempfile
from pathlib import Path
from email.message import Message
from unittest.mock import MagicMock
import app
import websearch


class SearchTests(unittest.TestCase):
    def test_greeting_does_not_make_assistant_name_a_news_anchor(self):
        query='早啊Aurora，帮我总结今天的新闻简报'
        rows=[{'title':'今日新闻简报','url':'https://example.org/news','snippet':'今日国际新闻与科技新闻'}]
        self.assertEqual(websearch.relevant(rows,query),rows)
        received=[]
        def provider(q):received.append(q);return rows
        with patch.object(websearch,'search_providers',return_value=(provider,)),patch.object(websearch,'read_page',return_value=('今日新闻简报','今日新闻正文 '*30)):
            self.assertTrue(websearch.search_pages(query))
        self.assertNotIn('Aurora',received[0]);self.assertNotIn('帮我',received[0])
        topic='搜索 Aurora 模型的最新新闻'
        self.assertEqual(websearch.relevant(rows,topic),[])

    def setUp(self):
        patcher=patch.object(websearch,'search_providers',side_effect=lambda:(websearch.duckduckgo,websearch.bing))
        patcher.start();self.addCleanup(patcher.stop)

    def test_domestic_parser_uses_real_result_destinations(self):
        parser=websearch.HeadingSearchParser('https://www.baidu.com/s')
        parser.feed('<div mu="https://example.org/zhuhai"><h3><a href="https://www.baidu.com/link?url=opaque">珠海<em>天气</em></a></h3><p>珠海气温</p></div>'
                    '<h3><a href="https://www.so.com/link?m=opaque" data-mdurl="https://example.org/gz">广州天气</a></h3>')
        self.assertEqual([r['url'] for r in parser.rows],['https://example.org/zhuhai','https://example.org/gz'])
        self.assertIn('珠海',parser.rows[0]['title'])

    def test_weather_excerpt_prefers_today_over_nearby_temperature_lists(self):
        primary='成都 城区 今天 24日（今天） 阴 20℃ 25日（明天） 阴 22℃ / 17℃\n'
        text=primary+('生活提示 穿衣出行\n'*70)+'周边城市 成都气温 降水 天气\n'+('周边景点 17/22℃ 16/21℃\n'*90)
        result=websearch.page_excerpt(text,'成都 今天天气 气温 降水',required_place=['成都'])
        self.assertIn('24日（今天）',result);self.assertIn('阴 20℃',result)

    def test_navigation_only_weather_page_does_not_count_as_weather_data(self):
        rows=[{'title':'成都天气预报','url':'https://example.org/navigation','snippet':'成都气温'},
              {'title':'成都实时天气','url':'https://example.org/data','snippet':'成都气温'}]
        with patch.object(websearch,'duckduckgo',return_value=rows),patch.object(websearch,'read_page',side_effect=[('成都天气预报','成都 实时天气 今日导航 '*50),('成都实时天气','成都 今天 阴 20℃ '*50)]):
            result=websearch.search_pages('成都 天气 气温',['成都'])
        self.assertEqual(result[0]['url'],'https://example.org/data')
        self.assertTrue(any('正文未读到' in s for s in websearch.LAST_SEARCH['steps']))

    def test_domestic_search_can_work_when_foreign_provider_is_unavailable(self):
        row={'title':'成都今天天气','url':'https://example.org/cd','snippet':'成都气温'}
        def domestic(_):return [row]
        def foreign(_):raise TimeoutError()
        with patch.object(websearch,'search_providers',return_value=(foreign,domestic)) as providers:
            providers.side_effect=None
            with patch.object(websearch,'read_page',return_value=('成都今天天气','成都20℃ '*50)):
                result=websearch.search_pages('成都今天天气',['成都'])
        self.assertIn('成都',result[0]['title'])
        self.assertTrue(any('超时' in s for s in websearch.LAST_SEARCH['steps']))

    def test_proxy_fake_ip_is_verified_with_public_dns(self):
        fake=[(2,1,6,'',('198.18.0.8',443))]
        with patch.object(websearch.socket,'getaddrinfo',return_value=fake),patch.object(websearch,'public_dns',return_value=['8.8.8.8']) as resolve:
            websearch.validate_public_url('https://example.org/')
            resolve.assert_called_once_with('example.org')
        with self.assertRaises(ValueError):websearch.validate_public_url('https://198.18.0.8/')

    def test_private_dns_result_cannot_be_replaced_by_public_dns(self):
        private=[(2,1,6,'',('127.0.0.1',443))]
        with patch.object(websearch.socket,'getaddrinfo',return_value=private),patch.object(websearch,'public_dns') as resolve:
            with self.assertRaises(ValueError):websearch.validate_public_url('https://example.org/')
            resolve.assert_not_called()

    def test_page_parser_excludes_navigation_and_hidden_other_cities(self):
        parser=websearch.PageParser()
        parser.feed('<html><head><title>珠海天气</title><meta name="description" content="成都旧数据"></head><body>'
                    '<div class="city-box"><div><a>成都 20.1℃</a></div></div>'
                    '<nav><div>广州 22℃</div></nav><div style="display:none">北京 10℃</div>'
                    '<main><h1>珠海天气</h1><p>2026-10-24 多云 25℃</p></main>'
                    '<div class="recommend">山东天气</div></body></html>')
        text=''.join(parser.parts)
        self.assertIn('珠海',text);self.assertIn('25℃',text)
        for place in ['成都','广州','北京','山东']:self.assertNotIn(place,text)

    def test_current_weather_rejects_historical_page(self):
        row={'title':'珠海天气预报','url':'https://example.org/zh','snippet':'珠海气温'}
        with patch.object(websearch,'duckduckgo',return_value=[row]),patch.object(websearch,'bing',return_value=[]),patch.object(websearch,'read_page',return_value=('珠海历史天气','珠海去年20℃ '*50)):
            with self.assertRaisesRegex(RuntimeError,'没有沿用'):
                websearch.search_pages('珠海今天天气',['珠海'])

    def test_multiple_places_cannot_silently_return_only_one(self):
        row={'title':'珠海天气','url':'https://example.org/zh','snippet':'珠海气温'}
        with patch.object(websearch,'duckduckgo',return_value=[row]),patch.object(websearch,'bing',return_value=[]),patch.object(websearch,'read_page',return_value=('珠海天气','珠海25℃ '*50)):
            with self.assertRaisesRegex(RuntimeError,'没有沿用'):
                websearch.search_pages('珠海和广州天气',['珠海','广州'])

    def test_private_urls_rejected_before_request(self):
        for url in ['http://127.0.0.1/a','http://[::1]/a','http://169.254.169.254/','http://192.168.1.1','http://localhost/a','file:///tmp/a']:
            with self.assertRaises(ValueError):websearch.validate_public_url(url)

    def test_current_city_rejects_off_place_search_results(self):
        rows=[{'title':'成都天气','url':'https://example.org/cd','snippet':'成都 20℃'},
              {'title':'珠海天气','url':'https://example.org/zh','snippet':'珠海 25℃'}]
        self.assertEqual([r['title'] for r in websearch.relevant(rows,'珠海天气',['珠海'])],['珠海天气'])

    def test_actual_destination_title_must_match_not_just_search_label(self):
        response=MagicMock();response.url='https://example.org/weather'
        response.headers=Message();response.headers['Content-Type']='text/html; charset=utf-8'
        response.read.return_value=('<html><title>成都天气</title><p>珠海导航 '+('成都 20℃ '*30)+'</p></html>').encode()
        response.__enter__.return_value=response
        opener=MagicMock();opener.open.return_value=response
        with patch.object(websearch,'validate_public_url'),patch.object(websearch.urllib.request,'build_opener',return_value=opener):
            with self.assertRaisesRegex(ValueError,'地点'):
                websearch.read_page(response.url,['珠海'])

    def test_page_reads_are_fresh_when_city_changes(self):
        queries=[]
        def provider(query):
            queries.append(query)
            city=query[:2]
            return [{'title':city+'天气','url':'https://example.org/'+city,'snippet':city+'气温'}]
        with patch.object(websearch,'duckduckgo',side_effect=provider),patch.object(websearch,'read_page',side_effect=lambda url,p:(p[0]+'天气',p[0]+' 25℃ '*50)) as read:
            for city in ['珠海','广州']:
                result=websearch.search_pages(city+'天气',[city])
                self.assertIn(city,result[0]['title']);self.assertTrue(result[0]['read'])
            self.assertEqual(read.call_count,2)
        self.assertEqual(queries,['珠海天气','广州天气'])

    def test_no_matching_page_fails_without_reusing_other_city(self):
        row={'title':'成都天气','url':'https://example.org/cd','snippet':'成都气温'}
        with patch.object(websearch,'duckduckgo',return_value=[row]),patch.object(websearch,'bing',return_value=[row]),patch.object(websearch,'read_page') as read:
            with self.assertRaisesRegex(RuntimeError,'没有沿用'):
                websearch.search_pages('珠海天气',['珠海'])
            read.assert_not_called()

    def test_direct_public_page_is_read(self):
        with patch.object(websearch,'read_page',return_value=('珠海天气','珠海 25℃ '*50)) as read,patch.object(websearch,'duckduckgo') as search:
            result=websearch.search_pages('读取 https://example.org/weather',['珠海'])
            read.assert_called_once_with('https://example.org/weather',['珠海'])
            self.assertTrue(result[0]['read']);search.assert_not_called()

    def test_identity_does_not_search_but_current_events_do(self):
        for q in ['你是谁？','你现在是什么模型','你是哪个公司开发的？','你好']:
            self.assertFalse(websearch.needs_search(q),q)
        self.assertTrue(websearch.needs_search('Gemma 4最新发布进展'))

    def test_unrelated_node_results_are_rejected(self):
        rows=[{'title':'Node.js Tutorial','snippet':'Learn JavaScript backend development','url':'https://example.org/node'}]
        self.assertEqual(websearch.relevant(rows,'Gemma 4模型是谁开发的'),[])

    def test_weather_requires_city_and_recognizes_followup(self):
        _,message=websearch.prepare_query('今天天气怎么样',[])
        self.assertEqual(message,websearch.WEATHER_CLARIFICATION)
        query,message=websearch.prepare_query('成都',[{'role':'assistant','content':websearch.WEATHER_CLARIFICATION}])
        self.assertIsNone(message)
        self.assertIn('成都',query)
        self.assertIn('天气',query)

    def test_weather_rejects_calendar_and_car_sources(self):
        rows=[{'title':'今天黄历','snippet':'今天历史事件与日期','url':'https://example.org/today'},
              {'title':'2001 Jeep Grand Cherokee','snippet':'今天的汽车价格','url':'https://example.org/car'}]
        self.assertEqual(websearch.relevant(rows,'今天天气怎么样'),[])

    def test_weather_clarifies_before_network_or_model_generation(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DATA',Path(tmp)),patch.object(app,'model_info',return_value='gemma-4-12b'),patch.object(app,'search_web') as search,patch.object(app,'request_json') as generate:
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('天气测试',app.now())).lastrowid
            response=app.chat(sid,'今天天气怎么样',None,'gemma',True)
            self.assertIn('本轮尚未获取到当前位置',response['answer'])
            search.assert_not_called()
            generate.assert_not_called()

    def test_named_model_anchor_is_required(self):
        rows=[{'title':'Node.js 模型开发','snippet':'开发教程','url':'https://example.org/node'}]
        self.assertEqual(websearch.relevant(rows,'Gemma 4模型是谁开发的'),[])

    def test_reset_retries_then_falls_back(self):
        result={'title':'Gemma 4 by Google','snippet':'Gemma model details','url':'https://example.org/gemma'}
        with patch.object(websearch,'bing',side_effect=ConnectionResetError()),patch.object(websearch,'duckduckgo',return_value=[result]),patch.object(websearch.time,'sleep'):
            self.assertEqual(websearch.search('Gemma 4模型'),[result])

    def test_irrelevant_primary_uses_secondary(self):
        result={'title':'Gemma 4','snippet':'Google model','url':'https://example.org/gemma'}
        with patch.object(websearch,'bing',return_value=[{'title':'Node.js','url':'https://example.org/node','snippet':'JavaScript'}]),patch.object(websearch,'duckduckgo',return_value=[result]):
            self.assertEqual(websearch.search('Gemma 4模型'),[result])

    def test_failure_is_clear_without_fabricated_sources(self):
        with patch.object(websearch,'bing',side_effect=urllib.error.URLError('reset')),patch.object(websearch,'duckduckgo',return_value=[]),patch.object(websearch.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'未取得可靠结果'):
                websearch.search('Gemma 4')

    def test_secondary_parser_handles_redirects_and_markup(self):
        page=b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fgemma">Gemma <b>4</b></a><a class="result__snippet">Google model</a>'
        with patch.object(websearch,'read_url',return_value=page):
            result=websearch.duckduckgo('Gemma')
        self.assertEqual(result[0]['url'],'https://example.org/gemma')
        self.assertEqual(result[0]['title'],'Gemma 4')
        self.assertIn('Google',result[0]['snippet'])


if __name__=='__main__':
    unittest.main()
