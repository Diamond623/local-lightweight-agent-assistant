"""New model wiring and safe fallback, using temporary files and mocked processes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app


class LocalModelTests(unittest.TestCase):
    def test_availability_requires_both_complete_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(app, 'QWEN35_DIR', root), patch.object(app, 'QWEN35_FILES', (('weights', 4), ('projector', 3))):
                self.assertFalse(app.qwen35_available())
                (root/'weights').write_bytes(b'GGUF')
                (root/'projector').write_bytes(b'GG')
                self.assertFalse(app.qwen35_available())
                (root/'projector').write_bytes(b'GGU')
                self.assertTrue(app.qwen35_available())

    def test_identity_and_vision_are_checked(self):
        for identity, modalities, expected in [
            ('wrong-model', ['text', 'image'], None),
            ('qwen3.5-9b-q4_K_M', ['text'], None),
            ('qwen3.5-9b-q4_K_M', ['text', 'image'], 'qwen3.5-9b-q4_K_M'),
        ]:
            result = {'data': [{'id': identity, 'architecture': {'input_modalities': modalities}}]}
            with patch.object(app, 'request_json', return_value=result):
                self.assertEqual(app.model_info('qwen35'), expected)

    def test_new_model_has_bounded_context_and_stops_only_owned_process(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Mock(); old.poll.return_value = None
            with patch.object(app, 'DATA', Path(folder)), patch.object(app, 'LLAMA', '/usr/bin/true'), \
                 patch.object(app, 'PROCESSES', {'gemma': old}), patch.object(app, 'model_info', return_value=None), \
                 patch.object(app, 'qwen35_available', return_value=True), patch.object(app.subprocess, 'Popen') as launch:
                app.start_model('qwen35')
                args = launch.call_args.args[0]
                self.assertEqual(args[args.index('-c')+1], '8192')
                self.assertEqual(args[args.index('--parallel')+1], '1')
                self.assertEqual(args[args.index('--host')+1], '127.0.0.1')
                self.assertIn('--mmproj', args)
                self.assertIn('{"enable_thinking":false}', args)
                old.terminate.assert_called_once()

    def test_incomplete_download_preserves_running_fallback(self):
        old = Mock(); old.poll.return_value = None
        with patch.object(app, 'LLAMA', '/usr/bin/true'), patch.object(app, 'PROCESSES', {'gemma': old}), \
             patch.object(app, 'model_info', return_value=None), patch.object(app, 'qwen35_available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '原有模型仍可使用'):
                app.start_model('qwen35')
            old.terminate.assert_not_called()
        self.assertEqual(app.PORTS['text'], 8080)
        self.assertEqual(app.PORTS['gemma'], 8082)


if __name__ == '__main__':
    unittest.main()
