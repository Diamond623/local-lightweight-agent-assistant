"""Sparse model availability, launch configuration and UI regression."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class SparseModelTests(unittest.TestCase):
    def test_incomplete_weight_is_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            weight = Path(tmp) / 'model.gguf'
            with patch.object(app, 'MOE_MODEL', weight), patch.object(app, 'MOE_SIZE', 8):
                self.assertFalse(app.moe_available())
                weight.write_bytes(b'GGUFtest')
                self.assertTrue(app.moe_available())
                weight.write_bytes(b'bad!test')
                self.assertFalse(app.moe_available())

    def test_launch_is_bounded_and_text_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(app, 'DATA', Path(tmp)), patch.object(app, 'LLAMA', '/usr/bin/true'), \
                 patch.object(app, 'PROCESSES', {}), patch.object(app, 'port_in_use', return_value=False), \
                 patch.object(app, 'model_info', return_value=None), patch.object(app, 'moe_available', return_value=True), \
                 patch.object(app.subprocess, 'Popen') as launch:
                app.start_model('moe')
                args = launch.call_args.args[0]
                for flag, value in [('-c', '4096'), ('-ngl', '99'), ('--parallel', '1'),
                                    ('--alias', 'aurora-qwen35-moe'), ('-m', str(app.MOE_MODEL))]:
                    self.assertEqual(args[args.index(flag) + 1], value)
                self.assertNotIn('--mmproj', args)

    def test_ui_offers_sparse_model(self):
        html = (Path(app.__file__).parent / 'index.html').read_text()
        self.assertIn('<option value="moe">', html)
        self.assertIn("['text','qwen27b','moe'].includes", html)


if __name__ == '__main__':
    unittest.main()
