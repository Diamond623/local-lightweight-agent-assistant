import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
import local_clock


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.fixed = datetime.datetime(2026, 11, 9, 12, 34, 56, tzinfo=datetime.timezone.utc)

    def test_system_clock_and_explicit_east_eight_are_distinct(self):
        west = datetime.timezone(datetime.timedelta(hours=-5))
        with patch.object(local_clock, 'utc_now', return_value=self.fixed), \
             patch.object(local_clock, 'system_time', side_effect=lambda moment: moment.astimezone(west)):
            self.assertEqual(local_clock.answer('现在几点'),
                             '现在是 2026年11月9日 星期一 07:34:56（本机系统时区，UTC-05:00）。')
            self.assertEqual(local_clock.answer('北京现在几点'), None)
            self.assertEqual(local_clock.answer('北京时间现在几点'),
                             '现在是 2026年11月9日 星期一 20:34:56（东八区，UTC+08:00）。')
            self.assertEqual(local_clock.answer('现在UTC几点'),
                             '现在是 2026年11月9日 星期一 12:34:56（UTC，UTC+00:00）。')

    def test_timezone_followup_only_for_a_recent_clock_question(self):
        recent = [{'role': 'assistant', 'content': '现在是 07:34'},
                  {'role': 'user', 'content': '现在几点'}]
        with patch.object(local_clock, 'utc_now', return_value=self.fixed):
            self.assertIn('20:34:56', local_clock.answer('东八区', recent))
            self.assertIn('20:34:56', local_clock.answer('UTC+8', recent))
            self.assertIsNone(local_clock.answer('东八区'))
            self.assertIsNone(local_clock.answer('东八区', [{'role': 'user', 'content': '解释一下时区'}]))
            self.assertIsNone(local_clock.answer('解释东八区是什么', recent))
            self.assertIsNone(local_clock.answer('把“现在几点”翻译成英语'))
            self.assertIsNone(local_clock.answer('现在几点的英语怎么说'))

    def test_chat_saves_real_clock_without_starting_model_search_or_memory(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DATA', Path(folder)):
            with app.database() as db:
                sid = db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',
                                 ('新对话', app.now())).lastrowid
            with patch.object(local_clock, 'utc_now', return_value=self.fixed), \
                 patch.object(app, 'model_info', side_effect=AssertionError('model called')), \
                 patch.object(app, 'start_model', side_effect=AssertionError('model started')), \
                 patch.object(app, 'search_web', side_effect=AssertionError('search called')), \
                 patch.object(app.memory_engine, 'recall', side_effect=AssertionError('memory recalled')):
                first = app.chat(sid, '现在几点', None, 'gemma', True)
                second = app.chat(sid, '东八区', None, 'gemma', False)
            self.assertEqual(first['route'], 'clock')
            self.assertEqual(second['route'], 'clock')
            self.assertIn('20:34:56', second['answer'])
            self.assertEqual(first['sources'], [])
            self.assertEqual(first['memory_changes'], [])
            with app.database() as db:
                messages = list(db.execute('SELECT role,content FROM messages WHERE session_id=? ORDER BY id', (sid,)))
                self.assertEqual([r['role'] for r in messages], ['user', 'assistant', 'user', 'assistant'])
                self.assertEqual(messages[2]['content'], '东八区')
                self.assertEqual(db.execute('SELECT title FROM sessions WHERE id=?', (sid,)).fetchone()[0], '现在几点')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_fts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
