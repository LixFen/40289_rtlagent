import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from serve.environment import assigned_cards, runtime_environment


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root/'manifest.json').write_text(json.dumps({'model':{'name':'test-model'}}))
        (self.root/'serve').mkdir()
        (self.root/'serve/runtime.json').write_text(json.dumps({'model':{'file':'test.gguf'}}))
        self.dev = self.root/'dev'; self.dev.mkdir()
        self.sys = self.root/'sys'; self.sys.mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def card(self, name, vendor='0x1002', visible=True):
        device = self.sys/name/'device'
        device.mkdir(parents=True)
        (device/'vendor').write_text(vendor)
        (device/'mem_info_vram_used').write_text('1024')
        if visible:
            (self.dev/name).touch()

    def values(self, env=None):
        return runtime_environment(env or {},self.root,self.dev,self.sys)

    def test_image_defaults_use_models_mount_and_current_repository(self):
        values = self.values()
        self.assertEqual(Path(values['MODEL_PATH']),Path('/models/test.gguf'))
        self.assertEqual(values['INFERENCE_START'],(self.root/'serve/llamacpp.sh').as_posix())
        self.assertNotIn('/tmp/rtl-runtime',json.dumps(values))
        self.assertEqual(values['RUN_ARCHIVE_MB'],'8192')
        self.assertEqual(values['HF_HUB_OFFLINE'],'1')

    def test_installed_vivado_is_detected_when_base_environment_omits_variable(self):
        runtime = json.loads((self.root/'serve/runtime.json').read_text())
        runtime['final_target'] = {'vivado':'2026.1'}
        (self.root/'serve/runtime.json').write_text(json.dumps(runtime))
        tools = self.root/'tools'
        vivado = tools/'2026.1/Vivado'
        (vivado/'bin').mkdir(parents=True)
        (vivado/'bin/vivado').touch()
        self.assertEqual(self.values({'XILINX_ROOT':str(tools)})['XILINX_VIVADO'], str(vivado))

    def test_existing_platform_values_win_and_secrets_are_not_projected(self):
        env={'MODEL_PATH':'/workspace/models/model.gguf','LLAMA_SERVER':'/custom/llama-server',
             'SLANG_BIN':'/custom/slang','INFERENCE_START':'/custom/start.sh',
             'GPU_DRM_CARD':'card9','GPU_ASSIGNED_COUNT':'1','EDA_TMP':'/custom/eda',
             'AGENT_TMP':'/custom/agent','RUN_ARCHIVE_MB':'4096',
             'FPGACHINA_TOKEN':'never-print-this','CLOUD_LLM_API_KEY':'also-private',
             'XILINXD_LICENSE_FILE':'private-license'}
        values=self.values(env)
        for key in ('MODEL_PATH','LLAMA_SERVER','SLANG_BIN','INFERENCE_START','GPU_DRM_CARD','GPU_ASSIGNED_COUNT','RUN_ARCHIVE_MB'):
            self.assertEqual(values[key],env[key])
        self.assertEqual(Path(values['RUN_ARCHIVE_DIR']),Path('/custom/agent/rtl-runs'))
        self.assertEqual(Path(values['GPU_TELEMETRY_FILE']),Path('/custom/eda/rtl-gpu.json'))
        for key in ('FPGACHINA_TOKEN','CLOUD_LLM_API_KEY','XILINXD_LICENSE_FILE'):
            self.assertNotIn(key,values)
            self.assertNotIn(env[key],json.dumps(values))

    def test_gpu_detection_uses_visible_amd_cards_not_host_sysfs(self):
        self.card('card0',vendor='0x1a03')
        self.card('card4')
        self.card('card8',visible=False)
        self.assertEqual(assigned_cards(self.dev,self.sys),['card4'])
        values=self.values()
        self.assertEqual(values['GPU_DRM_CARD'],'card4')
        self.assertEqual(values['GPU_ASSIGNED_COUNT'],'1')

    def test_zero_or_multiple_cards_do_not_select_arbitrary_gpu(self):
        self.assertNotIn('GPU_DRM_CARD',self.values())
        self.assertEqual(self.values()['GPU_ASSIGNED_COUNT'],'0')
        self.card('card2'); self.card('card4')
        values=self.values()
        self.assertNotIn('GPU_DRM_CARD',values)
        self.assertEqual(values['GPU_ASSIGNED_COUNT'],'2')

    def test_sdk_library_default_preserves_existing_search_path(self):
        site=self.root/'site-packages'
        sdk=site/'_rocm_sdk_devel/lib'; sdk.mkdir(parents=True)
        with patch('serve.environment.sysconfig.get_path',return_value=str(site)):
            self.assertEqual(self.values()['LD_LIBRARY_PATH'],str(sdk))
            self.assertEqual(self.values({'LD_LIBRARY_PATH':'/existing/lib'})['LD_LIBRARY_PATH'],'/existing/lib')

    def test_shell_output_quotes_literal_values_and_omits_credentials(self):
        repo=Path(__file__).resolve().parents[1]
        literal="/models/name ' $(printf injected).gguf"
        env=dict(os.environ,MODEL_PATH=literal,FPGACHINA_TOKEN='never-print-this',
                 CLOUD_LLM_API_KEY='also-private',XILINXD_LICENSE_FILE='private-license')
        result=subprocess.run([sys.executable,'-m','serve.environment','--shell'],
                              cwd=repo,env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('export MODEL_PATH=',result.stdout)
        for secret in ('never-print-this','also-private','private-license'):
            self.assertNotIn(secret,result.stdout)
        self.assertIn("'\"'\"'",result.stdout)

    def test_container_env_example_has_no_active_token_or_fixed_card(self):
        example=(Path(__file__).resolve().parents[1]/'serve/container.env.example').read_text()
        active=[line for line in example.splitlines() if line and not line.startswith('#')]
        self.assertFalse(any(line.startswith(('FPGACHINA_TOKEN=','GPU_DRM_CARD=')) for line in active))
        self.assertTrue(all('=' in line and not line.startswith('export ') for line in active))

    def test_shell_preserves_license_without_installing_fallback_before_probe(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('bash unavailable')
        repo = Path(__file__).resolve().parents[1]
        shim = self.root/'python3'
        shim.write_text('#!/usr/bin/env bash\nexec "$TEST_PYTHON" "$@"\n', encoding='utf-8')
        shim.chmod(0o755)
        env = dict(os.environ,TEST_PYTHON=Path(sys.executable).as_posix(),
                   PATH=self.root.as_posix()+os.pathsep+os.environ.get('PATH',''),MODEL_PATH='/models/mock.gguf',
                   AGENT_TMP=str(self.root/'agent-state'))
        env.pop('XILINXD_LICENSE_FILE',None)
        env.pop('FLEXLM_TIMEOUT',None)
        command = '''cd "$1"; source serve/env.sh; "$TEST_PYTHON" -c 'import os,json; print(json.dumps([os.environ.get("XILINXD_LICENSE_FILE"),os.environ.get("FLEXLM_TIMEOUT")]))' '''
        for overrides,expected in [({},[None,None]),
                                   ({'XILINXD_LICENSE_FILE':'test-license','FLEXLM_TIMEOUT':'42'},['test-license','42'])]:
            with self.subTest(overrides=overrides):
                result=subprocess.run([str(bash),'-c',command,'environment-test',repo.as_posix()],
                                      env=dict(env,**overrides),capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),expected)

    def test_platform_template_loads_vivado_before_api_inherits_environment(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('bash unavailable')
        repo = Path(__file__).resolve().parents[1]
        fake_repo = self.root/'repository'
        (fake_repo/'serve').mkdir(parents=True)
        (fake_repo/'serve/config.sh').write_text('export CONFIG_LOADED=1\n')
        vivado = self.root/'Vivado'
        vivado.mkdir()
        (vivado/'.settings64-Vivado.sh').write_text('export VIVADO_ENV_LOADED=1\n')
        env = dict(os.environ, RTL_AGENT_ROOT=fake_repo.as_posix(), XILINX_VIVADO=vivado.as_posix())
        result = subprocess.run([str(bash), '-c',
            'source "$1"; test "$VIVADO_ENV_LOADED" = 1 && test "$CONFIG_LOADED" = 1',
            'platform-environment-test', (repo/'serve/platform.env.example.sh').as_posix()],
            env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
