import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import download_model


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status=status
        self.headers=headers or {}


class DownloadTests(unittest.TestCase):
    def test_resume_verifies_before_installing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(download_model,'ROOT',Path(tmp)):
            data=b'GGUF'+b'example-weights'*20
            part=Path(tmp)/'test.gguf.partial'
            part.write_bytes(data[:10])
            def open_url(req,timeout=30):
                self.assertEqual(req.get_header('Range'),'bytes=10-')
                return Response(data[10:],206,{'Content-Range':f'bytes 10-{len(data)-1}/{len(data)}'})
            with patch.object(download_model.urllib.request,'urlopen',side_effect=open_url):
                download_model.download('test.gguf',len(data),hashlib.sha256(data).hexdigest())
            self.assertEqual((Path(tmp)/'test.gguf').read_bytes(),data)
            self.assertFalse(part.exists())

    def test_ignored_range_replaces_partial(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(download_model,'ROOT',Path(tmp)):
            data=b'GGUFcomplete'
            (Path(tmp)/'test.gguf.partial').write_bytes(b'GGU')
            with patch.object(download_model.urllib.request,'urlopen',return_value=Response(data)):
                download_model.download('test.gguf',len(data),hashlib.sha256(data).hexdigest())
            self.assertEqual((Path(tmp)/'test.gguf').read_bytes(),data)

    def test_checksum_failure_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(download_model,'ROOT',Path(tmp)):
            with patch.object(download_model.urllib.request,'urlopen',return_value=Response(b'bad')):
                with self.assertRaisesRegex(RuntimeError,'SHA-256'):
                    download_model.download('test.gguf',3,hashlib.sha256(b'yes').hexdigest())
            self.assertFalse((Path(tmp)/'test.gguf').exists())


if __name__=='__main__':
    unittest.main()
