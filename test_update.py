from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import install_update

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'package';self.target=self.root/'installed';self.source.mkdir();self.target.mkdir()
        for name in install_update.FILES:
            (self.source/name).parent.mkdir(parents=True,exist_ok=True)
            (self.source/name).write_text("BUILD = 'new'\n" if name.endswith('.py') else 'new')
        (self.source/'Aurora.app/Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.aurora.assistant','CFBundleIconFile':'Aurora.icns'}))
        (self.target/'app.py').write_text("BUILD = 'old'\n")
        plist=self.target/'Aurora.app/Contents/Info.plist';plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.aurora.assistant'}))
        (self.target/'models').mkdir();(self.target/'models/weights.gguf').write_bytes(b'EXISTING MODEL')
        (self.target/'data').mkdir();(self.target/'data/memory.sqlite3').write_bytes(b'EXISTING PRIVATE DATA')
    def tearDown(self):self.temp.cleanup()
    def test_update_preserves_data_models_and_backs_up_code(self):
        backup=install_update.install(self.source,self.target)
        self.assertEqual((self.target/'app.py').read_text(),"BUILD = 'new'\n")
        self.assertEqual((backup/'app.py').read_text(),"BUILD = 'old'\n")
        self.assertFalse((backup/'Aurora.app').exists())
        self.assertTrue((backup/'Aurora-bundle-backup/Contents/Info.plist').is_file())
        self.assertEqual((self.target/'data/memory.sqlite3').read_bytes(),b'EXISTING PRIVATE DATA')
        self.assertEqual((self.target/'models/weights.gguf').read_bytes(),b'EXISTING MODEL')
    def test_syntax_failure_does_not_touch_installation(self):
        (self.source/'generation.py').write_text('def broken(')
        with self.assertRaises(SyntaxError):install_update.install(self.source,self.target)
        self.assertEqual((self.target/'app.py').read_text(),"BUILD = 'old'\n")
        self.assertFalse((self.target/'code-backups').exists())
    def test_copy_failure_rolls_back_changed_code(self):
        actual=install_update.atomic_copy;calls=[]
        def fail(source,target):
            calls.append(source.name)
            if len(calls)==3:raise OSError('simulated disk failure')
            return actual(source,target)
        with patch.object(install_update,'atomic_copy',side_effect=fail):
            with self.assertRaises(OSError):install_update.install(self.source,self.target)
        self.assertEqual((self.target/'app.py').read_text(),"BUILD = 'old'\n")
        self.assertFalse((self.target/'conversation.py').exists())
        self.assertEqual((self.target/'data/memory.sqlite3').read_bytes(),b'EXISTING PRIVATE DATA')
    def test_wrong_target_is_rejected(self):
        (self.target/'Aurora.app/Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'another.app'}))
        with self.assertRaises(ValueError):install_update.install(self.source,self.target)

    def test_icon_copy_failure_restores_old_bundle_configuration(self):
        binary=self.target/'Aurora.app/Contents/MacOS/Aurora'
        binary.parent.mkdir(parents=True,exist_ok=True);binary.write_bytes(b'OLD NATIVE WINDOW')
        original=(self.target/'Aurora.app/Contents/Info.plist').read_bytes()
        actual=install_update.atomic_copy
        def fail(source,target):
            if source.name=='Aurora.icns':raise OSError('simulated icon copy failure')
            return actual(source,target)
        with patch.object(install_update,'atomic_copy',side_effect=fail):
            with self.assertRaises(OSError):install_update.install(self.source,self.target)
        self.assertEqual((self.target/'Aurora.app/Contents/Info.plist').read_bytes(),original)
        self.assertEqual((self.target/'app.py').read_text(),"BUILD = 'old'\n")
        self.assertEqual(binary.read_bytes(),b'OLD NATIVE WINDOW')

if __name__=='__main__':unittest.main()
