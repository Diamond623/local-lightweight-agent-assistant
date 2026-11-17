import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import cleanup_unused_models as cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.root=self.home/'.ollama/models'
        self.blobs=self.root/'blobs';self.blobs.mkdir(parents=True)
        self.old='sha256:'+'a'*64;self.shared='sha256:'+'b'*64
        for digest in (self.old,self.shared):
            (self.blobs/digest.replace(':','-')).write_bytes(b'GGUFtest')
        def manifest(name,layers):
            p=self.root/'manifests'/name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(json.dumps({'config':{'digest':self.shared},'layers':[{'digest':d} for d in layers]}))
            return p
        self.retired=manifest('registry.ollama.ai/library/qwen3/8b',[self.old,self.shared])
        self.kept=manifest('registry.ollama.ai/library/qwen3.5/9b-q4_K_M',[self.shared])
        self.cache=self.home/'.cache/huggingface/hub/models--Qwen--Qwen3-VL-4B-Instruct-GGUF'
        self.cache.mkdir(parents=True);(self.cache/'partial').write_bytes(b'partial')
        self.home_patch=patch.object(Path,'home',return_value=self.home);self.home_patch.start()
        self.protect=patch.object(cleanup,'PROTECTED',{self.shared});self.protect.start()
    def tearDown(self):
        self.protect.stop();self.home_patch.stop();self.temp.cleanup()
    def test_apply_keeps_shared_layers_and_kept_manifest(self):
        with patch.object(cleanup.subprocess,'run',return_value=Mock(stdout='',returncode=1)),contextlib.redirect_stdout(io.StringIO()):
            cleanup.clean(True)
        self.assertFalse(self.retired.exists());self.assertTrue(self.kept.exists())
        self.assertFalse((self.blobs/self.old.replace(':','-')).exists())
        self.assertTrue((self.blobs/self.shared.replace(':','-')).exists())
        self.assertFalse(self.cache.exists())
    def test_dry_run_never_removes_anything(self):
        with contextlib.redirect_stdout(io.StringIO()):cleanup.clean(False)
        self.assertTrue(self.retired.exists());self.assertTrue(self.cache.exists())
    def test_in_use_model_blocks_every_deletion(self):
        with patch.object(cleanup.subprocess,'run',return_value=Mock(stdout='123',returncode=0)),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'正在使用'):cleanup.clean(True)
        self.assertTrue(self.retired.exists());self.assertTrue(self.cache.exists())


if __name__=='__main__':unittest.main()
