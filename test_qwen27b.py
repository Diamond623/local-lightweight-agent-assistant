"""27B registration, ownership, bounded requests and reasoning regressions."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import app
import generation
from test_generation import sse


class Qwen27BTests(unittest.TestCase):
    def test_weight_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            weight=Path(tmp)/'model'
            with patch.object(app,'QWEN27B_MODEL',weight),patch.object(app,'QWEN27B_SIZE',8):
                self.assertFalse(app.qwen27b_available())
                weight.write_bytes(b'GGUFtest');self.assertTrue(app.qwen27b_available())
                weight.write_bytes(b'not-gguf');self.assertFalse(app.qwen27b_available())

    def test_identity_is_exact(self):
        for name,expected in [('wrong',None),('aurora-qwen27b','aurora-qwen27b')]:
            with patch.object(app,'request_json',return_value={'data':[{'id':name}]}):
                self.assertEqual(app.model_info('qwen27b'),expected)

    def test_reuse_unloads_only_owned_siblings(self):
        old=Mock();old.poll.return_value=None
        with patch.object(app,'PROCESSES',{'gemma':old}),patch.object(app,'port_in_use',return_value=False),patch.object(app,'model_info',return_value='aurora-qwen27b'),patch.object(app.subprocess,'Popen') as launch:
            self.assertIn('复用',app.start_model('qwen27b'))
            old.terminate.assert_called_once();old.wait.assert_called_once();launch.assert_not_called()

    def test_external_loading_service_blocks_both_directions_even_ready_target(self):
        for target,external in [('gemma','qwen27b'),('qwen27b','gemma')]:
            with patch.object(app,'PROCESSES',{}),patch.object(app,'port_in_use',side_effect=lambda mode:mode==external),patch.object(app,'model_info',return_value='ready'),patch.object(app.subprocess,'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError,'不会终止'):app.start_model(target)
                launch.assert_not_called()

    def test_wrong_service_on_target_is_not_replaced(self):
        with patch.object(app,'PROCESSES',{}),patch.object(app,'port_in_use',side_effect=lambda mode:mode=='qwen27b'),patch.object(app,'model_info',return_value=None),patch.object(app.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'8084'):app.start_model('qwen27b')
            launch.assert_not_called()

    def test_launch_uses_existing_blob_metal_and_8192_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Mock();old.poll.return_value=None
            with patch.object(app,'DATA',Path(tmp)),patch.object(app,'LLAMA','/usr/bin/true'),patch.object(app,'PROCESSES',{'gemma':old}),patch.object(app,'port_in_use',return_value=False),patch.object(app,'model_info',return_value=None),patch.object(app,'qwen27b_available',return_value=True),patch.object(app.subprocess,'Popen') as launch:
                app.start_model('qwen27b');args=launch.call_args.args[0]
                for flag,value in [('-c','8192'),('-ngl','99'),('--parallel','1'),('--alias','aurora-qwen27b'),('-m',str(app.QWEN27B_MODEL)),('-fa','on')]:self.assertEqual(args[args.index(flag)+1],value)
                self.assertNotIn('--mmproj',args);self.assertNotIn('-hf',args);old.terminate.assert_called_once()
                self.assertEqual(args[args.index('--reasoning-budget')+1],'1024')
                self.assertIn('{"enable_thinking":true}',args)

    def test_reasoning_never_becomes_answer(self):
        events=[]
        body=sse({'choices':[{'delta':{'reasoning_content':'private-thought'}}]}, {'choices':[{'delta':{'content':'最终答案'}}]}, {'choices':[{'delta':{},'finish_reason':'stop'}]})
        with patch('urllib.request.urlopen',return_value=body):
            result=generation.completion('http://model',{},events.append,lambda:False)
        self.assertEqual(result['choices'][0]['message']['content'],'最终答案')
        self.assertEqual([e['text'] for e in events if e['type']=='delta'],['最终答案'])
        self.assertNotIn('private-thought',str(events))

    def test_token_budget_discards_old_history_not_current_question(self):
        calls=[]
        def api(url,payload,cancelled,timeout=10):
            calls.append((url,payload))
            if url.endswith('apply-template'):return {'prompt':str(payload['messages'])}
            return {'tokens':list(range(1800 if len(calls)==2 else 1000))}
        props=io.BytesIO(json.dumps({'default_generation_settings':{'n_ctx':2048}}).encode())
        messages=[{'role':'system','content':'rules'},{'role':'user','content':'old'},{'role':'assistant','content':'old answer'},{'role':'user','content':'current'}]
        with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=api):
            payload=generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':messages,'max_tokens':750},lambda:False)
        self.assertEqual(payload['max_tokens'],512);self.assertEqual(payload['messages'],[messages[0],messages[-1]])
        self.assertEqual(len(messages),4)

    def test_overflow_is_reported_without_truncating_question(self):
        props=io.BytesIO(b'{"default_generation_settings":{"n_ctx":2048}}')
        with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=[{'prompt':'long'},{'tokens':[0]*1600}]):
            with self.assertRaisesRegex(ValueError,'2048'):
                generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':[{'role':'user','content':'long'}]},lambda:False)

    def test_8192_context_preserves_long_prompt_and_2048_output(self):
        props=io.BytesIO(b'{"default_generation_settings":{"n_ctx":8192}}')
        messages=[{'role':'system','content':'rules'},{'role':'user','content':'long current question'}]
        with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=[{'prompt':'rendered'},{'tokens':[0]*5500}]):
            payload=generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':messages,'max_tokens':2048},lambda:False)
        self.assertEqual(payload['max_tokens'],2048)
        self.assertEqual(payload['messages'],messages)

    def test_8192_reserves_output_budget_and_refuses_oversized_current_question(self):
        props=io.BytesIO(b'{"default_generation_settings":{"n_ctx":8192}}')
        with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=[{'prompt':'rendered'},{'tokens':[0]*6200}]):
            with self.assertRaisesRegex(ValueError,'8192'):
                generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':[{'role':'user','content':'current'}],'max_tokens':2048},lambda:False)

    def test_thinking_is_bounded_but_helper_tasks_stay_off(self):
        for thinking,maximum,budget in [(True,3072,1024),(False,2048,0)]:
            props=io.BytesIO(b'{"default_generation_settings":{"n_ctx":8192}}')
            with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=[{'prompt':'rendered'},{'tokens':[0]*100}]):
                payload=generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':[{'role':'user','content':'test'}],'max_tokens':9000,'chat_template_kwargs':{'enable_thinking':thinking}},lambda:False)
            self.assertIs(payload['chat_template_kwargs']['enable_thinking'],thinking)
            self.assertEqual(payload['max_tokens'],maximum)
            self.assertEqual(payload['reasoning_budget_tokens'],budget)

    def test_thinking_reserves_space_for_both_reasoning_and_answer(self):
        props=io.BytesIO(b'{"default_generation_settings":{"n_ctx":8192}}')
        with patch('urllib.request.urlopen',return_value=props),patch.object(generation,'request_json',side_effect=[{'prompt':'rendered'},{'tokens':[0]*5100}]):
            with self.assertRaisesRegex(ValueError,'3072'):
                generation.prepare_payload('http://model/v1/chat/completions',{'model':'aurora-qwen27b','messages':[{'role':'user','content':'long current'}],'max_tokens':3072,'chat_template_kwargs':{'enable_thinking':True}},lambda:False)

    def test_original_ports_and_menu_remain(self):
        self.assertEqual(app.PORTS,{'text':8080,'vision':8081,'gemma':8082,'qwen35':8083,'qwen27b':8084})
        html=(app.ROOT/'index.html').read_text()
        for mode in ('qwen35','gemma','qwen27b'):self.assertIn(f'<option value="{mode}"',html)
        for mode in ('text','vision'):self.assertNotIn(f'<option value="{mode}"',html)
        self.assertIn('深度思考 · Qwen3.8 27B',html)
        self.assertIn("['text','qwen27b'].includes($('mode').value)",html)

if __name__=='__main__':unittest.main()
