import json
import unittest
from unittest.mock import patch, Mock
import desktop_control
import agent
import generation

class DesktopTests(unittest.TestCase):
    def test_rejects_arbitrary_apps_and_commands(self):
        with patch.object(desktop_control.sys,'platform','darwin'), patch.object(desktop_control.subprocess,'run') as run:
            for action,arg in [('open_app','终端'),('open_app','/tmp/app'),('shell','rm -rf')]:
                with self.assertRaises(ValueError): desktop_control.execute(action,arg)
            run.assert_not_called()
    def test_fixed_arguments_without_shell(self):
        with patch.object(desktop_control.sys,'platform','darwin'), patch.object(desktop_control.subprocess,'run',return_value=Mock(returncode=0,stdout='{"opened":"Calculator"}')) as run:
            self.assertIn('Calculator',desktop_control.execute('open_app','计算器'))
            self.assertEqual(run.call_args.args[0][1:],['open','calculator'])
            self.assertNotIn('shell',run.call_args.kwargs)
    def test_click_requires_bounded_index_and_exact_title(self):
        with patch.object(desktop_control.sys,'platform','darwin'),patch.object(desktop_control.subprocess,'run',return_value=Mock(returncode=0,stdout='{"clicked":"7"}')) as run:
            desktop_control.execute('click_ui','{"index":1,"title":"7"}')
            self.assertEqual(run.call_args.args[0][1:],['click','1','7'])
            for text in ('{"index":-1,"title":"7"}','{"index":1.5,"title":"7"}','{"index":3,"title":""}'):
                with self.assertRaises(ValueError):desktop_control.execute('click_ui',text)
    def test_cancel_prevents_action(self):
        with patch.object(desktop_control.subprocess,'run') as run:
            with self.assertRaises(generation.Cancelled): desktop_control.execute('open_app','计算器',lambda:True)
            run.assert_not_called()
    def test_agent_desktop_is_explicitly_granted(self):
        for enabled in (False, True):
            replies=iter(['{"action":"open_app","query":"计算器"}','{"action":"finish"}']); calls=[]
            r=agent.run('打开计算器',lambda _:next(replies),lambda *a:[],False,[],[],lambda:False,lambda _:None,
                        desktop=(lambda *a:calls.append(a) or '已打开') if enabled else None)
            self.assertEqual(len(calls),int(enabled))
            self.assertEqual(r['trace'][0]['status'],'completed' if enabled else 'skipped')
    def test_click_requires_inspection_in_same_run(self):
        replies=iter(['{"action":"click_ui","query":"{\\"index\\":0,\\"title\\":\\"7\\"}"}',
                      '{"action":"inspect_ui","query":"查看按钮"}',
                      '{"action":"click_ui","query":"{\\"index\\":0,\\"title\\":\\"7\\"}"}',
                      '{"action":"finish"}']);calls=[]
        result=agent.run('查看并点击计算器的7',lambda _:next(replies),lambda *a:[],False,[],[],lambda:False,lambda _:None,
                         desktop=lambda *a:calls.append(a) or '{"buttons":[{"index":0,"title":"7"}]}')
        self.assertEqual([x[0] for x in calls],['inspect_ui','click_ui'])
        self.assertEqual(result['trace'][0]['status'],'skipped')
