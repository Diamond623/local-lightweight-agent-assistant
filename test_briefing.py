import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
import scheduled_tasks as tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=patch.object(app,'DATA',Path(self.tmp.name));self.data.start()
        self.notifications=patch.object(tasks.subprocess,'run');self.notifications.start()
    def tearDown(self):
        self.notifications.stop();self.data.stop();self.tmp.cleanup()
    def stamp(self,value): return dt.datetime.fromisoformat(value).timestamp()
    def save(self,**changes):
        task=dict(name='练习提醒',prompt='复习 C 语言数组',kind='reminder',repeat='daily',clock='08:00',date='')
        task.update(changes);tasks.api('save-task',task)
        with app.database() as db:return dict(db.execute("SELECT * FROM scheduled_tasks WHERE name='练习提醒'").fetchone())
    def test_beijing_time_and_weekend_schedule(self):
        friday=self.stamp('2026-10-23T08:01:00+08:00')
        self.assertEqual(tasks.next_time('08:00','weekdays',after=friday),self.stamp('2026-10-26T08:00:00+08:00'))
        self.assertEqual(tasks.next_time('08:00','daily',after=friday),self.stamp('2026-10-24T08:00:00+08:00'))
        with self.assertRaises(ValueError):tasks.next_time('08:00','once','2026-10-22',friday)
    def test_validation_and_pause_resume(self):
        row=self.save()
        with self.assertRaises(ValueError):self.save(id=row['id'],prompt='')
        tasks.api('toggle-task',{'id':row['id']})
        with app.database() as db:self.assertEqual(db.execute('SELECT enabled FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()[0],0)
        tasks.api('toggle-task',{'id':row['id']})
        with app.database() as db:self.assertEqual(db.execute('SELECT enabled FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()[0],1)
    def test_once_reminder_catchup_and_duplicate_prevention(self):
        row=self.save();now=dt.datetime.now().timestamp()
        with app.database() as db:
            db.execute('UPDATE scheduled_tasks SET enabled=0')
            db.execute("UPDATE scheduled_tasks SET enabled=1,repeat='once',next_run=? WHERE id=?",(now-3600,row['id']))
        with patch.object(app,'start_model') as model:
            tasks.tick(now);tasks.tick(now+60);model.assert_not_called()
        with app.database() as db:
            task=db.execute('SELECT * FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()
            self.assertEqual(task['enabled'],0);self.assertEqual(task['status'],'已完成')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
            self.assertIn('复习 C 语言数组',db.execute('SELECT content FROM messages').fetchone()[0])
    def test_failure_is_visible_and_retry_is_backed_off(self):
        row=self.save(kind='research');now=dt.datetime.now().timestamp()
        with app.database() as db:
            db.execute('UPDATE scheduled_tasks SET enabled=0')
            db.execute('UPDATE scheduled_tasks SET enabled=1,next_run=? WHERE id=?',(now-60,row['id']))
        with patch('daily_briefing.produce',side_effect=RuntimeError('网络不可用')) as run:
            tasks.tick(now);tasks.tick(now+60);self.assertEqual(run.call_count,1)
            tasks.tick(now+1801);self.assertEqual(run.call_count,2)
        with app.database() as db:self.assertIn('网络不可用',db.execute('SELECT last_error FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()[0])
    def test_deleting_task_preserves_existing_results(self):
        row=self.save();sid=tasks.reminder(row,'test-run','2026-10-25')
        tasks.api('delete-task',{'id':row['id']})
        with app.database() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone())
        other=self.save();self.assertNotEqual(other['id'],row['id'])
    def test_weekday_task_does_not_catch_up_on_sunday(self):
        row=self.save(repeat='weekdays');sunday=self.stamp('2026-10-25T09:00:00+08:00')
        with app.database() as db:
            db.execute('UPDATE scheduled_tasks SET enabled=0')
            db.execute('UPDATE scheduled_tasks SET enabled=1,next_run=? WHERE id=?',(sunday-172800,row['id']))
        tasks.tick(sunday)
        with app.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT next_run FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()[0],self.stamp('2026-10-26T08:00:00+08:00'))


if __name__=='__main__':unittest.main()
