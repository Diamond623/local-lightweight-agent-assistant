from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import native_build

class NativeBuildTests(unittest.TestCase):
    def fixture(self,path):
        files=('Desktop.swift','Aurora.app/Contents/MacOS/Aurora','Aurora.app/Contents/Info.plist','Aurora.app/Contents/_CodeSignature/CodeResources')
        for name in files:
            p=path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'original')
        (path/files[2]).write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.aurora.assistant'}))
        return files
    def test_staged_compile_sign_and_verify_never_modify_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder);files=self.fixture(source)
            with patch.object(native_build,'command') as run:
                with native_build.prepare(source,files,rebuild=True) as staged:
                    self.assertNotEqual(staged,source)
                    self.assertEqual(plistlib.loads((staged/files[2]).read_bytes())['CFBundleVersion'],'9.26')
                    self.assertTrue((staged/files[1]).stat().st_mode&0o111)
                    calls=[call.args[0] for call in run.call_args_list]
                    self.assertIn('swiftc',calls[0]);self.assertIn('--sign',calls[1]);self.assertIn('--verify',calls[2])
            self.assertEqual((source/files[1]).read_bytes(),b'original')
            self.assertNotIn('CFBundleVersion',plistlib.loads((source/files[2]).read_bytes()))
    def test_compile_or_sign_failure_prevents_install(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder);files=self.fixture(source)
            for effects in ([RuntimeError('compiler failed')],[None,RuntimeError('signing failed')]):
                with patch.object(native_build,'command',side_effect=effects):
                    with self.assertRaises(RuntimeError):
                        with native_build.prepare(source,files,rebuild=True):self.fail('Must not expose failed build to installer')
                self.assertEqual((source/files[1]).read_bytes(),b'original')
    def test_native_edit_menu_targets_standard_responder_actions(self):
        source=(Path(__file__).parent/'Desktop.swift').read_text()
        for action,key in [('cut','x'),('copy','c'),('paste','v'),('selectAll','a')]:
            self.assertIn('#selector(NSText.'+action+'(_:)), keyEquivalent: "'+key+'"',source)
        self.assertIn('NSApp.mainMenu = mainMenu',source)
        self.assertIn('installMenus()',source)

if __name__=='__main__':unittest.main()
