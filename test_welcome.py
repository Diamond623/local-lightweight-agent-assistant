from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
import welcome


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=patch.object(welcome,'SETTINGS',self.root/'welcome.json');self.settings.start()
        self.data=patch.object(app,'DATA',self.root/'data');self.data.start()
    def tearDown(self):self.data.stop();self.settings.stop();self.tmp.cleanup()
    def test_only_explicit_manual_name_is_used(self):
        with app.database() as db:
            db.execute('INSERT INTO messages(role,content) VALUES(?,?)',('assistant','你叫错误名字'))
            db.execute('INSERT INTO memories(content) VALUES(?)',('我喜欢写程序',))
        self.assertEqual(welcome.read()['nickname'],'')
        with app.database() as db:db.execute('INSERT INTO memories(content) VALUES(?)',('叫我小林，用中文回答',))
        self.assertEqual(welcome.read()['nickname'],'小林')
    def test_preference_overrides_notes_and_can_be_cleared(self):
        with app.database() as db:db.execute('INSERT INTO memories(content) VALUES(?)',('我叫小林',))
        welcome.save('艺焜');self.assertEqual(welcome.read()['nickname'],'艺焜')
        welcome.save('');self.assertEqual(welcome.read()['nickname'],'')
    def test_invalid_names_do_not_replace_existing_preference(self):
        welcome.save('艺焜')
        for value in ['太长'*11,'名字\n换行',None]:
            with self.assertRaises(ValueError):welcome.save(value)
        self.assertEqual(welcome.read()['nickname'],'艺焜')


if __name__=='__main__':unittest.main()
