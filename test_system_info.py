import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
import app
import system_info


class SystemInfoTests(unittest.TestCase):
    def test_only_explicit_local_questions_select_allowlisted_topics(self):
        self.assertEqual(system_info.direct_topics('我电脑的配置是什么'), system_info.ALL)
        self.assertEqual(system_info.direct_topics('我电脑的电池还有多少电'), ('battery',))
        self.assertEqual(system_info.direct_topics('那电池呢？'), ('battery',))
        self.assertEqual(system_info.direct_topics('Aurora 现在占了多少内存'), ('memory', 'aurora'))
        self.assertEqual(system_info.direct_topics('系统信息'), system_info.ALL)
        self.assertEqual(system_info.direct_topics('内存管理的原理是什么'), ())
        self.assertEqual(system_info.direct_topics('帮我写一段关于电脑的广告'), ())
        self.assertEqual(system_info.direct_topics('能不能读取我电脑的信息'), ())
        self.assertEqual(system_info.topics('根据我的电脑配置推荐合适的模型'), system_info.ALL)

    def test_reads_fixed_commands_and_labels_estimates(self):
        outputs = {
            ('/usr/bin/sw_vers', '-productVersion'): '27.2\n',
            ('/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'): 'Apple M5 Pro\n',
            ('/usr/sbin/sysctl', '-n', 'hw.memsize'): str(24*1024**3),
            ('/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'): '1',
            ('/usr/sbin/sysctl', 'vm.swapusage'): 'total = 2.00G used = 512.00M free = 1.50G',
            ('/usr/bin/vm_stat',): ('page size of 16384 bytes\nPages free: 1000.\n'
                                    'Pages wired down: 200.\nAnonymous pages: 100.\n'
                                    'Pages occupied by compressor: 50.'),
            ('/usr/bin/pmset', '-g', 'batt'): "Now drawing from 'AC Power'\n -InternalBattery-0\t72%; charging; 0:00 remaining",
        }
        with patch.object(system_info, '_command', side_effect=lambda args: outputs.get(tuple(args))), \
             patch.object(system_info.shutil, 'disk_usage', return_value=type('Disk',(),{'total':1024**4,'used':200*1024**3,'free':824*1024**3})()):
            sample = system_info.collect(system_info.ALL, question='查看我电脑的配置')
        self.assertEqual(sample['values']['memory']['total_bytes'], 24*1024**3)
        self.assertEqual(sample['values']['disk']['free_bytes'], 824*1024**3)
        self.assertEqual(sample['values']['battery']['percentage'], 72)
        reply = system_info.describe(sample, system_info.ALL)
        self.assertIn('Apple M5 Pro',reply)
        self.assertIn('24.0 GB',reply)
        self.assertIn('824.0 GB',reply)
        self.assertIn('空闲页不等于可用内存',reply)

    def test_failed_reads_are_unknown_and_do_not_expose_paths_or_fake_zero(self):
        with patch.object(system_info, '_command', return_value=None), \
             patch.object(system_info.shutil, 'disk_usage', side_effect=OSError('private-path')):
            sample = system_info.collect(system_info.ALL,question='查看电脑信息')
        self.assertIsNone(sample['values']['memory']['total_bytes'])
        self.assertIsNone(sample['values']['cpu']['chip'])
        reply = system_info.describe(sample, system_info.ALL)
        self.assertIn('暂不可读',reply)
        self.assertNotIn('private-path',reply)

    def test_profile_fallback_discards_serial_number(self):
        raw = json.dumps({'SPHardwareDataType':[{'chip_type':'Apple M5 Pro',
                   'physical_memory':'24 GB','serial_number':'DO-NOT-RETURN-THIS'}]})
        with patch.object(system_info, '_command', side_effect=lambda args: raw if 'system_profiler' in args[0] else None):
            sample = system_info.collect(('cpu','memory'))
        response = system_info.describe(sample,('cpu','memory'))
        self.assertIn('Apple M5 Pro',response)
        self.assertIn('24.0 GB',response)
        self.assertNotIn('DO-NOT-RETURN-THIS',str(sample))

    def test_unavailable_sensors_are_named_without_guessing(self):
        with patch.object(system_info,'_command',return_value=None):
            for question, term in [('CPU温度是多少','CPU 温度或频率'),('电池健康度多少','电池健康度与循环次数')]:
                requested=system_info.direct_topics(question)
                self.assertTrue(requested)
                self.assertIn(term,system_info.describe(system_info.collect(requested,question=question),requested))

    def test_direct_chat_bypasses_model_and_does_not_index_transient_metrics(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DATA', Path(folder)):
            with app.database() as db:
                sid = db.execute("INSERT INTO sessions(title,created) VALUES('新对话','now')").lastrowid
            sample = {'sampled_at':'2026-11-09T23:00:00+08:00',
                      'values':{'battery':{'percentage':88,'power':'电池','charging':False}},'unavailable':[]}
            with patch.object(app.system_info, 'collect', return_value=sample) as read, \
                 patch.object(app, 'model_info', side_effect=AssertionError('model called')), \
                 patch.object(app, 'search_web', side_effect=AssertionError('web called')):
                result = app.chat(sid,'我电脑电池还有多少电',None,'gemma',True,agent_mode=True)
            self.assertEqual(result['route'],'system')
            self.assertIn('88%',result['answer'])
            self.assertEqual(result['agent_trace']['trace'][0]['tool'],'read_system_info')
            self.assertEqual(read.call_args.kwargs['question'],'我电脑电池还有多少电')
            with app.database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE session_id=?',(sid,)).fetchone()[0],2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_fts').fetchone()[0],0)
                trace=json.loads(db.execute("SELECT agent_trace FROM messages WHERE role='assistant'").fetchone()[0])
                self.assertEqual(trace['trace'][0]['status'],'completed')

    def test_analytical_chat_supplies_fresh_system_evidence_to_the_model(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DATA', Path(folder)):
            with app.database() as db:
                sid = db.execute("INSERT INTO sessions(title,created) VALUES('新对话','now')").lastrowid
            sample = {'sampled_at':'2026-11-09T23:00:00+08:00',
                      'values':{'memory':{'total_bytes':24*1024**3,'used_estimate_bytes':None,
                                          'free_pages_bytes':None,'pressure':None},
                                'system':{'macOS':'27.2','architecture':'arm64'},
                                'cpu':{'chip':'Apple M5 Pro','logical_cores':15},
                                'disk':{'total_bytes':1024**4,'used_bytes':200*1024**3,'free_bytes':824*1024**3},
                                'battery':{'percentage':88,'power':'电池','charging':False}},'unavailable':[]}
            def generate(url,payload,timeout=240):
                system = payload['messages'][0]['content']
                self.assertIn('24.0 GB',system)
                self.assertIn('Apple M5 Pro',system)
                self.assertIn('本轮由只读系统接口实时读取',system)
                return {'choices':[{'message':{'content':'本轮分析基于实测配置。'},'finish_reason':'stop'}]}
            with patch.object(app.system_info,'collect',return_value=sample), \
                 patch.object(app,'model_info',return_value='fixture'), \
                 patch.object(app,'start_model'),patch.object(app,'request_json',side_effect=generate):
                result=app.chat(sid,'根据我的电脑配置推荐合适的模型',None,'gemma',False)
            self.assertIn('实测配置',result['answer'])
            self.assertEqual(result['route'],'local')

    def test_agent_reads_only_supplied_snapshot_once(self):
        actions=iter(['{"action":"read_system_info","query":"current"}', '{"action":"finish"}'])
        seen=[]
        result=agent.run('读取我的电脑信息',lambda _:next(actions),lambda *_:self.fail('no web'),False,[],[],
                         lambda:False,lambda _:None,read_system_info=lambda:seen.append('read') or '内存 24 GB')
        self.assertEqual(seen,['read'])
        self.assertEqual(result['trace'][0]['tool'],'read_system_info')
        self.assertIn('24 GB',result['observations'][0])
        forbidden=iter(['{"action":"read_system_info","query":"current"}', '{"action":"finish"}'])
        result=agent.run('解释内存是什么',lambda _:next(forbidden),lambda *_:self.fail('no web'),False,[],[],
                         lambda:False,lambda _:None)
        self.assertEqual(result['trace'][0]['status'],'skipped')


if __name__ == '__main__':
    unittest.main()
