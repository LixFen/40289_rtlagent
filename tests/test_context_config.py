"""上下文配置回归；启动参数检查使用替身程序，不代表真实模型验收。"""
from contextlib import contextmanager
import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.config import load_settings, load_runtime
from agent.llm.client import ModelClient
from agent.loop.context import Conversation
from tools.serve_development import parse_args
from agent.loop.lifecycle import RunControl
from tests.protocol import completion


ROOT = Path(__file__).resolve().parents[1]


class ContextConfigTests(unittest.TestCase):
    def test_official_manifest_fields_drive_defaults_and_round_limit(self):
        manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
        runtime = load_runtime(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'serve').mkdir()
            manifest['serving']['port'] = 9123
            manifest['agent']['max_rounds'] = 7
            manifest['context'].update(max_model_len=49152, max_output_tokens=12000)
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'serve/runtime.json').write_text(json.dumps(runtime))
            with patch('agent.config.ROOT', root), patch.dict(os.environ, {}, clear=True):
                settings = load_settings()
                self.assertEqual(settings.base_url, 'http://127.0.0.1:9123/v1')
                self.assertEqual((settings.context_tokens, settings.max_output_tokens), (49152,12000))
                self.assertEqual(settings.input_char_limit, runtime['agent']['input_char_limit'])
                self.assertEqual(settings.max_response_bytes, runtime['agent']['max_response_bytes'])
                self.assertEqual(RunControl().max_calls, 7)
                self.assertEqual(parse_args([]).reasoning_effort, manifest['generation']['reasoning_effort'])

    def test_environment_overrides_do_not_evaluate_missing_manifest_fields(self):
        env = {'LLM_BASE_URL':'http://127.0.0.1:9001/v1','MODEL_NAME':'override',
               'AGENT_CONTEXT_TOKENS':'32768','AGENT_MAX_OUTPUT_TOKENS':'10000',
               'AGENT_INPUT_CHAR_LIMIT':'20000','AGENT_MAX_RESPONSE_BYTES':'30000',
               'AGENT_MAX_MODEL_CALLS':'5'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'manifest.json').write_text('{}')
            with patch('agent.config.ROOT', root), patch.dict(os.environ, env, clear=True):
                settings = load_settings()
                self.assertEqual((settings.base_url, settings.model_name), (env['LLM_BASE_URL'],'override'))
                self.assertEqual((settings.input_char_limit, settings.max_response_bytes), (20000,30000))
                self.assertEqual(RunControl().max_calls, 5)

    def test_runtime_preserves_thinking_without_extra_manifest_fields(self):
        with patch.dict(os.environ, {}, clear=True):
            model = ModelClient()
        self.assertTrue(model.preserve_thinking)
        self.assertNotIn('preserve_thinking', model.generation['chat_template_kwargs'])
        with patch.dict(os.environ, {'AGENT_PRESERVE_THINKING':'0'}):
            self.assertFalse(ModelClient().preserve_thinking)

    @contextmanager
    def configuration(self):
        """为边界行为提供固定测试配置，不依赖生产默认值。"""
        manifest = {'model':{'name':'local-test-model'}, 'serving':{'base_url':'http://127.0.0.1:8000/v1'},
                    'development':{'base_url':'https://cloud.test/v1','model_name':'cloud-test-model'}, 'context':{'max_model_len':65536, 'max_output_tokens':65535},
                    'agent':{'max_rounds':24, 'input_char_limit':65536, 'max_response_bytes':1048576}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with patch('agent.config.ROOT', root):
                yield root, manifest

    def test_editing_only_manifest_updates_all_python_consumers(self):
        with self.configuration() as (root, manifest), patch.dict(os.environ, {}, clear=True):
            for context, output, input_limit, response_bytes in [(32768,12000,22000,1000000), (49152,16000,30000,2000000)]:
                manifest['context'].update(max_model_len=context, max_output_tokens=output)
                manifest['agent'].update(input_char_limit=input_limit, max_response_bytes=response_bytes)
                (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                manifest['model']['name'] = 'local-'+str(context)
                manifest['serving']['base_url'] = 'http://127.0.0.1:'+str(9000+context//16384)+'/v1'
                manifest['development'].update(base_url='https://cloud-'+str(context)+'.test/v1', model_name='cloud-'+str(context))
                (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                model = ModelClient()
                conversation, args = Conversation('task', '', False), parse_args([])
                self.assertEqual((model.context_tokens, model.max_output, model.response_bytes), (context, output, response_bytes))
                self.assertEqual(conversation.input_char_limit, input_limit)
                self.assertEqual((args.context_tokens, args.output_tokens), (context, output))
                self.assertEqual((model.base, model.name), (manifest['serving']['base_url'], manifest['model']['name']))
                self.assertEqual((args.upstream, args.model), (manifest['development']['base_url'], manifest['development']['model_name']))

    def test_environment_and_cli_override_manifest_defaults(self):
        with self.configuration(), patch.dict(os.environ, {'AGENT_CONTEXT_TOKENS':'32768', 'AGENT_MAX_OUTPUT_TOKENS':'7000', 'LLM_BASE_URL':'http://127.0.0.1:8123/v1/', 'MODEL_NAME':'env-model', 'CLOUD_LLM_BASE_URL':'https://env-cloud.test/v1/'}, clear=True):
            model, args = ModelClient(), parse_args([])
            self.assertEqual((model.context_tokens, model.max_output), (32768,7000))
            self.assertEqual((model.base, model.name), ('http://127.0.0.1:8123/v1','env-model'))
            self.assertEqual((args.upstream, args.model), ('https://env-cloud.test/v1','env-model'))
            self.assertEqual((args.context_tokens, args.output_tokens), (32768,7000))
            args = parse_args(['--context-tokens','16384','--output-tokens','5000','--upstream','https://cli-cloud.test/v1','--model','cli-model'])
            self.assertEqual((args.context_tokens, args.output_tokens), (16384,5000))
            self.assertEqual((args.upstream, args.model), ('https://cli-cloud.test/v1','cli-model'))

    def test_health_and_startup_probe_follow_manifest_connection(self):
        from serve.health import health
        from serve.startup import models_ready
        with self.configuration() as (root, manifest), patch.dict(os.environ, {}, clear=True):
            telemetry = root/'gpu.json'
            telemetry.write_text(json.dumps({'ts':time.time(),'vram_gb':8,'device_count':1}))
            with patch.dict(os.environ, {'GPU_TELEMETRY_FILE':str(telemetry)}):
                for name, port in [('first-model', 9101), ('second-model', 9102)]:
                    manifest['model']['name'] = name
                    manifest['serving']['base_url'] = f'http://127.0.0.1:{port}/v1'
                    (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                    def models(url, **_):
                        self.assertEqual(url, manifest['serving']['base_url']+'/models')
                        return io.BytesIO(json.dumps({'data':[{'id':name}]}).encode())
                    with patch('urllib.request.urlopen', side_effect=models):
                        self.assertTrue(models_ready())
                        self.assertEqual(health()['model'], name)
                        self.assertTrue(health()['ready'])
                with patch.dict(os.environ, {'MODEL_NAME':'undeclared-model'}), \
                     patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"data":[{"id":"undeclared-model"}]}')):
                    self.assertFalse(health()['ready'])

    def test_invalid_or_missing_config_does_not_use_hidden_defaults(self):
        with self.configuration() as (root, manifest):
            with self.assertRaises(ValueError):
                load_settings(environ={'AGENT_CONTEXT_TOKENS':'1; echo bad'})
            del manifest['context']['max_output_tokens']
            (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(KeyError):
                load_settings(environ={})

    def test_default_context_and_output_match_declaration(self):
        manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
        with patch.dict(os.environ, {}, clear=True):
            model = ModelClient()
        self.assertEqual(model.context_tokens, manifest['context']['max_model_len'])
        self.assertEqual(model.max_output, manifest['context']['max_output_tokens'])
        self.assertEqual((model.base, model.name), ('http://127.0.0.1:'+str(manifest['serving']['port'])+'/v1', manifest['model']['name']))

    def test_request_above_old_input_limit_can_proceed(self):
        with self.configuration(), patch.dict(os.environ, {}, clear=True):
            model = ModelClient()
            control = RunControl()
            body = model.create_request([{'role':'user','content':'x'*40000}], [])
            self.assertGreater(body['max_tokens'], 8192)
            reserved = model.input_tokens
            self.assertLessEqual(reserved+body['max_tokens'], model.context_tokens)
            self.assertTrue(control.accept_request(body))

    def test_later_request_ignores_cumulative_usage(self):
        with self.configuration(), patch.dict(os.environ, {}, clear=True):
            model, control = ModelClient(), RunControl()
            control.total_tokens = 1000000
            body = model.create_request([{'role':'user','content':'continue'}], [])
            configured = body['max_tokens']
            self.assertTrue(control.accept_request(body))
            self.assertEqual(body['max_tokens'], configured)
            self.assertLessEqual(model.input_tokens+body['max_tokens'], model.context_tokens)

    def test_context_overflow_trims_complete_history_before_rejecting(self):
        with self.configuration(), patch.dict(os.environ, {'AGENT_INPUT_CHAR_LIMIT':'1000000'}, clear=True):
            model = ModelClient()
            conversation = Conversation('task', '', False)
            conversation.history = [[{'role':'assistant', 'content':'old history'},
                                     {'role':'user', 'content':'old feedback'}]]
            runtime = SimpleNamespace(state=lambda:{'current':None}, disabled=set(),
                                      registry=SimpleNamespace(definitions=lambda _:[]))
            with patch.object(model.counter, 'count_input', side_effect=[(65536, 'test'), (65000, 'test')]):
                body = conversation.build_request(model, runtime)
            self.assertEqual(conversation.history, [])
            self.assertEqual(len(body['messages']), 3)
            self.assertEqual(body['max_tokens'], 536)
            self.assertTrue(RunControl().accept_request(body))

    def test_character_threshold_does_not_reject_input_that_fits_context(self):
        with self.configuration(), patch.dict(os.environ, {'AGENT_INPUT_CHAR_LIMIT':'10'}, clear=True):
            model = ModelClient()
            body = model.create_request([{'role':'user', 'content':'x'*70000}], [])
            self.assertGreater(len(json.dumps(body)), 65536)
            self.assertTrue(RunControl().accept_request(body))
            self.assertLessEqual(model.input_tokens+body['max_tokens'], model.context_tokens)

    def test_response_larger_than_old_byte_limit_is_preserved(self):
        content = 'x'*300000
        response = completion({'tool':'write_file','args':{'path':'notes/long.txt','content':content}}, usage={'total_tokens':100})
        with self.configuration(), patch.dict(os.environ, {}, clear=True), \
             patch('agent.llm.client.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            model = ModelClient()
            result, tokens = model.complete({}, ROOT, lambda *_a,**_k:None, 1, 100, 1)
        self.assertEqual(tokens, 100)
        self.assertEqual(model.parse_actions(result)[0][0]['args']['content'], content)

    def test_launchers_pass_shared_context_to_inference_process(self):
        if os.name == 'nt':
            bash = Path('C:/Program Files/Git/bin/bash.exe')
            if not bash.is_file():
                self.skipTest('Git Bash unavailable; actual Linux launcher check required')
        else:
            bash = shutil.which('bash')
            if not bash:
                self.skipTest('bash unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'agent').mkdir()
            (root/'serve').mkdir()
            for name in ('__init__.py','config.py','paths.py'):
                shutil.copyfile(ROOT/'agent'/name, root/'agent'/name)
            for name in ('__init__.py','config.sh','env.sh','environment.py','runtime.json'):
                (root/'serve'/name).write_bytes((ROOT/'serve'/name).read_bytes().replace(b'\r\n', b'\n'))
            manifest = {'model':{'name':'local-test-model'}, 'serving':{'base_url':'http://127.0.0.1:8000/v1'},
                    'development':{'base_url':'https://cloud.test/v1','model_name':'cloud-test-model'}, 'context':{'max_model_len':32768,'max_output_tokens':12000},
                        'agent':{'input_char_limit':22000,'max_response_bytes':1000000},
                        'generation':{'chat_template_kwargs':{'enable_thinking':True},'reasoning_effort':'low'}}
            python = root/'python3'
            python.write_text('#!/usr/bin/env bash\nif [[ "$*" == "-m serve.license_probe --shell" ]]; then exit 0; fi\nif [[ "$*" == "-m serve.startup" ]]; then\n  printf "%s\n" "$AGENT_CONTEXT_TOKENS" "$AGENT_MAX_OUTPUT_TOKENS" "$LLM_BASE_URL" "$MODEL_NAME" "$LLM_BIND_HOST" "$LLM_BIND_PORT" > "$CONFIG_CAPTURE"\n  exit 0\nfi\nexec "$TEST_PYTHON" "$@"\n', encoding='utf-8')
            python.chmod(0o755)
            capture = root/'arguments.txt'
            config_capture = root/'settings.txt'
            executable = root/'fake-server'
            executable.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ARG_CAPTURE"\nprintf "%s\\n" "$AGENT_CONTEXT_TOKENS" "$AGENT_MAX_OUTPUT_TOKENS" "$LLM_BASE_URL" "$MODEL_NAME" "$LLM_BIND_HOST" "$LLM_BIND_PORT" > "$CONFIG_CAPTURE"\n', encoding='utf-8')
            executable.chmod(0o755)
            weights = root/'model.gguf'; weights.write_bytes(b'test')
            model_dir = root/'model'; model_dir.mkdir()
            sdk = root/'sdk'
            loader = sdk/'compat/root/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
            loader.parent.mkdir(parents=True)
            shutil.copyfile(executable, loader); loader.chmod(0o755)
            (sdk/'llama/llama-b11552').mkdir(parents=True)
            library = sdk/'framework-venv/lib/python3.14/site-packages/_rocm_sdk_core/lib/libamdhip64.so.7'
            library.parent.mkdir(parents=True); library.touch()
            vllm = root/'vllm'
            shutil.copyfile(executable, vllm); vllm.chmod(0o755)
            for script_name, option in [('llamacpp.sh','--ctx-size'),
                                        ('llamacpp-sdk10.sh','--ctx-size'),
                                        ('vllm.sh','--max-model-len'), ('start.sh',None)]:
                script = root/'serve'/script_name
                script.write_bytes((ROOT/'serve'/script_name).read_bytes().replace(b'\r\n', b'\n'))
                for default_context, default_output, override, expected, expected_output in [
                        (32768,12000,None,'32768','12000'),
                        (49152,16000,None,'49152','16000'),
                        (49152,16000,'16384','16384','5000')]:
                    with self.subTest(script=script_name, context=expected, output=expected_output):
                        manifest['context'].update(max_model_len=default_context, max_output_tokens=default_output)
                        manifest['model']['name'] = "model ' $(printf injected)-"+str(default_context)
                        manifest['serving']['base_url'] = 'http://127.0.0.1:'+str(9000+default_context//16384)+'/v1'
                        expected_url, expected_model = manifest['serving']['base_url'], manifest['model']['name']
                        (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                        env = os.environ.copy()
                        for key in ('AGENT_CONTEXT_TOKENS','AGENT_MAX_OUTPUT_TOKENS','AGENT_INPUT_CHAR_LIMIT','AGENT_MAX_RESPONSE_BYTES','XILINX_VIVADO','LLM_BASE_URL','MODEL_NAME'):
                            env.pop(key, None)
                        env.update(LLAMA_SERVER=executable.as_posix(), MODEL_PATH=(model_dir if script_name=='vllm.sh' else weights).as_posix(),
                                   RTL_RUNTIME_ROOT=sdk.as_posix(), ARG_CAPTURE=capture.as_posix(), CONFIG_CAPTURE=config_capture.as_posix(),
                                   TEST_PYTHON=Path(sys.executable).as_posix(), EDA_TMP=(root/'eda').as_posix(), AGENT_TMP=(root/'runs').as_posix(),
                                   PATH=root.as_posix()+os.pathsep+env.get('PATH',''))
                        if override:
                            env.update(AGENT_CONTEXT_TOKENS=override, AGENT_MAX_OUTPUT_TOKENS=expected_output, LLM_BASE_URL='http://127.0.0.1:9200/v1', MODEL_NAME='env-model')
                            expected_url, expected_model = env['LLM_BASE_URL'], env['MODEL_NAME']
                        result = subprocess.run([str(bash), script.as_posix()], env=env,
                                                capture_output=True, text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(config_capture.read_text(encoding='utf-8').splitlines(), [expected,expected_output,expected_url,expected_model,'127.0.0.1',expected_url.split(':')[2].split('/')[0]])
                        if option:
                            args = capture.read_text(encoding='utf-8').splitlines()
                            self.assertEqual(args[args.index(option)+1], expected)
                            name_option = '--served-model-name' if script_name=='vllm.sh' else '--alias'
                            self.assertEqual(args[args.index(name_option)+1], expected_model)
                            self.assertEqual(args[args.index('--host')+1], '127.0.0.1')
                            self.assertEqual(args[args.index('--port')+1], expected_url.split(':')[2].split('/')[0])
                            if script_name == 'llamacpp.sh':
                                template = json.loads(args[args.index('--chat-template-kwargs')+1])
                                self.assertEqual(template['reasoning_effort'], manifest['generation']['reasoning_effort'])
                                self.assertTrue(template['preserve_thinking'])
                                self.assertEqual(args[args.index('--reasoning-budget')+1], '4096')
            # 配置加载失败时不能吞掉 Python 错误后启动推理程序。
            capture.unlink(missing_ok=True)
            (root/'manifest.json').write_text('{}', encoding='utf-8')
            result = subprocess.run([str(bash), (root/'serve/llamacpp.sh').as_posix()], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(capture.exists())


if __name__ == '__main__':
    unittest.main()
