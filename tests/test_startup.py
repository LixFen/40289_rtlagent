import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from agent.tooling.eda import EDA, directory_size, log_excerpt
from agent.tooling.workspace import Workspace
from serve.eda_cleanup import alive, sweep
from serve.startup import spawn
from serve.tasks.worker import stop
from serve.prepare_image import prepare
import hashlib
import subprocess
import signal
from fnmatch import fnmatchcase


class StartupTests(unittest.TestCase):
    def test_linux_zombie_service_is_not_alive(self):
        with patch('serve.eda_cleanup.os.name', 'posix'), \
             patch('serve.eda_cleanup.sys.platform', 'linux'), \
             patch('serve.eda_cleanup.os.kill'), \
             patch('serve.eda_cleanup.Path') as path:
            path.return_value.__truediv__.return_value.__truediv__.return_value.read_text.return_value = '123 (llama-server) Z 1 123'
            self.assertFalse(alive(123))
            path.return_value.__truediv__.return_value.__truediv__.return_value.read_text.return_value = '123 (llama-server) S 1 123'
            self.assertTrue(alive(123))

    def test_eda_preserves_license_timeout_without_forwarding_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root/'out'; out.mkdir()
            work = root/'work'; work.mkdir()
            eda = EDA(Workspace(out/'workspace'), out, time.monotonic()+10)
            proc = Mock(pid=43210)
            proc.poll.return_value = 0
            proc.wait.return_value = 0
            with patch.dict(os.environ, {'FLEXLM_TIMEOUT':'300000000',
                                         'FPGACHINA_TOKEN':'test-token', 'CLOUD_LLM_API_KEY':'test-key'}), \
                 patch.object(eda, 'executable', return_value='vivado'), \
                 patch('agent.tooling.eda.subprocess.Popen', return_value=proc) as popen, \
                 patch('agent.tooling.eda.register'), patch('agent.tooling.eda.terminate'):
                self.assertEqual(eda.run(work, 'vivado', [])['status'], 'completed')
            env = popen.call_args.kwargs['env']
            self.assertEqual(env['FLEXLM_TIMEOUT'], '300000000')
            self.assertNotIn('FPGACHINA_TOKEN', env)
            self.assertNotIn('CLOUD_LLM_API_KEY', env)

    def test_docker_copy_files_are_in_build_context(self):
        root = Path(__file__).resolve().parents[1]
        patterns = [line.strip() for line in (root/'.dockerignore').read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        # 当前构建使用正向全排除与逐项放行；核对 COPY 文件的实际包含顺序。
        for line in (root/'Dockerfile').read_text().splitlines():
            if not line.startswith('COPY '):
                continue
            parts = line.split()
            if any(part.startswith('--from=') for part in parts):
                continue  # Stage artifacts are not local build-context inputs.
            for name in (part for part in parts[1:-1] if not part.startswith('--')):
                path = root/name
                self.assertTrue(path.exists(), 'Missing COPY input: '+name)
                if path.is_dir():
                    continue
                included = True
                for pattern in patterns:
                    if fnmatchcase(name, pattern.lstrip('!')):
                        included = pattern.startswith('!')
                with self.subTest(source=name):
                    self.assertTrue(included, 'COPY input excluded by .dockerignore: '+name)

    def test_artifact_scan_tolerates_removed_files_but_reports_access_errors(self):
        removed = Mock()
        removed.is_file.return_value = True
        removed.stat.side_effect = FileNotFoundError('Vivado removed a temporary file')
        present = Mock()
        present.is_file.return_value = True
        present.stat.return_value.st_size = 123
        root = Mock()
        root.rglob.return_value = [removed, present]
        self.assertEqual(directory_size(root), 123)
        present.stat.side_effect = PermissionError('Cannot read artifact metadata')
        with self.assertRaises(PermissionError):
            directory_size(root)

    def test_tool_excerpt_is_bounded_in_utf8_bytes(self):
        result=log_excerpt('头'*1000+'middle'+'尾'*1000)
        self.assertLessEqual(len(result.encode('utf-8')),2048)
        self.assertTrue(result.startswith('头'))
        self.assertTrue(result.endswith('尾'))

    def test_image_baseline_matches_official_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'serve').mkdir()
            files={'baseline.py':b'print("baseline")\n','run_baseline.sh':b'exec python3 baseline.py\n'}
            for name,data in files.items():(root/name).write_bytes(data.replace(b'\n',b'\r\n'))
            hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
            (root/'manifest.json').write_text('{}')
            (root/'serve/runtime.json').write_text(json.dumps({'baseline_source':{'canonical_sha256':hashes}}))
            prepare(root)
            for name,data in files.items():self.assertEqual((root/name).read_bytes(),data)
            (root/'baseline.py').write_text('different code')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):prepare(root)
    def test_posix_timeout_signals_group_then_reaps(self):
        proc=Mock(pid=12345)
        proc.wait.side_effect=[subprocess.TimeoutExpired('worker',5),0]
        with patch('serve.tasks.worker.os.name','posix'),patch('serve.tasks.worker.os.killpg',create=True) as signal_group, \
             patch('serve.tasks.worker.terminate') as kill_group,patch('serve.tasks.worker.reap_group') as reap:
            stop(proc)
        signal_group.assert_called_once_with(proc.pid,signal.SIGTERM)
        kill_group.assert_called_once_with(proc.pid)
        reap.assert_called_once_with(proc.pid)

    def test_service_spawn_detaches_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            proc=Mock(pid=12345)
            started=[]
            with patch('serve.startup.subprocess.Popen',return_value=proc) as popen, \
                 patch('serve.startup.alive',return_value=True):
                spawn('model',['model-command'],root,started)
                spawn('model',['model-command'],root,started)
            self.assertEqual(popen.call_count,1)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(started,[proc])

    def test_eda_work_is_local_and_transient_evidence_is_small(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            out=root/'out';out.mkdir()
            temp=root/'local-eda'
            eda=EDA(Workspace(out/'workspace'),out,time.monotonic()+10)
            def operation(kind,args,source,contract,work):
                self.assertTrue(work.is_relative_to(temp.resolve()))
                (work/'candidate.sv').write_text('module TopModule; endmodule')
                (work/'xsim.dir').mkdir()
                (work/'xsim.dir/temp.bin').write_bytes(b'ephemeral')
                return {'status':'passed'}
            with patch.dict(os.environ,{'EDA_TMP':str(temp)}),patch.object(eda,'_operation',side_effect=operation):
                self.assertEqual(eda.operation('compile',{},None,None)['status'],'passed')
            self.assertEqual(list(temp.iterdir()),[])
            self.assertEqual(len(list((out/'evidence').glob('*/candidate.sv'))),1)
            self.assertFalse(list((out/'evidence').rglob('xsim.dir')))

    def test_stale_cleanup_checks_descendant_updates_and_active_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            stale=root/'rtl-eda-old';stale.mkdir()
            recent=root/'rtl-eda-recent';recent.mkdir()
            child=recent/'active.log';child.write_text('new')
            active=root/'rtl-eda-active';active.mkdir()
            (active/'owner.json').write_text(json.dumps({'pid':12345}))
            old=time.time()-8000
            for folder in (stale,recent,active):os.utime(folder,(old,old))
            with patch('serve.eda_cleanup.alive',return_value=True):sweep(root)
            self.assertFalse(stale.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(active.exists())
