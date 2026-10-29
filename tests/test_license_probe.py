import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from serve import license_probe as probe


class LicenseProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.binary = self.root/'vivado'
        self.binary.write_text('fake vivado')
        self.env = {'AGENT_TMP':str(self.root/'state'), 'EDA_TMP':str(self.root/'eda')}

    def tearDown(self):
        self.directory.cleanup()

    def run_probe(self, results, env=None):
        with patch.object(probe, 'executable', return_value=str(self.binary)), \
             patch.object(probe, 'synthesize', side_effect=results) as run:
            record = probe.ensure_license(self.env if env is None else env)
        return record, run

    def test_existing_license_passes_without_setting_official_address(self):
        env = dict(self.env, XILINXD_LICENSE_FILE='custom-license')
        record, run = self.run_probe([{'passed':True}], env)
        self.assertEqual(record['mode'], 'existing')
        self.assertEqual(record['exports'], {'XILINXD_LICENSE_FILE':'custom-license'})
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('FLEXLM_TIMEOUT', env)

    def test_no_license_variable_is_added_if_default_tool_lookup_succeeds(self):
        record, _ = self.run_probe([{'passed':True}])
        self.assertEqual(record['exports'], {})

    def test_failed_probe_uses_official_fallback_then_caches_success(self):
        record, run = self.run_probe([{'passed':False}, {'passed':True}])
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('XILINXD_LICENSE_FILE', run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0]['XILINXD_LICENSE_FILE'], probe.FALLBACK)
        self.assertEqual(record['mode'], 'fallback')
        for env in (self.env, dict(self.env, **record['exports'])):
            cached, run = self.run_probe([], env)
            self.assertTrue(cached['cached'])
            run.assert_not_called()

    def test_changed_license_or_binary_invalidates_cache(self):
        self.run_probe([{'passed':True}])
        with patch.object(probe, 'executable', return_value=str(self.binary)):
            self.assertIsNone(probe.cached_selection(dict(self.env, XILINXD_LICENSE_FILE='different')))
            self.binary.write_text('changed vivado binary')
            self.assertIsNone(probe.cached_selection(self.env))

    def test_repeated_official_configuration_is_not_tried_twice(self):
        env = dict(self.env, XILINXD_LICENSE_FILE=probe.FALLBACK, FLEXLM_TIMEOUT='300000000')
        with patch.object(probe, 'executable', return_value=str(self.binary)), \
             patch.object(probe, 'synthesize', return_value={'passed':False}) as run:
            with self.assertRaisesRegex(RuntimeError, 'synthesis licensing probe failed'):
                probe.ensure_license(env)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((probe.state_directory(env)/'success.json').exists())

    def test_zero_exit_without_completion_marker_is_not_success(self):
        root = self.root/'logs';root.mkdir()
        proc = Mock(returncode=0)
        with patch.object(probe.subprocess, 'Popen', return_value=proc):
            result = probe.synthesize(self.env, str(self.binary), root, 'test', 30)
        self.assertFalse(result['passed'])

    def test_marker_and_successful_exit_are_both_required(self):
        root = self.root/'logs';root.mkdir()
        def popen(*_args, **kwargs):
            kwargs['stdout'].write((probe.MARKER+'\n').encode())
            return Mock(returncode=0)
        with patch.object(probe.subprocess, 'Popen', side_effect=popen):
            result = probe.synthesize(self.env, str(self.binary), root, 'test', 30)
        self.assertTrue(result['passed'])

    def test_timeout_kills_only_owned_process_and_fails_probe(self):
        root = self.root/'logs';root.mkdir()
        proc = Mock(pid=12345, returncode=-9)
        proc.wait.side_effect = [subprocess.TimeoutExpired('vivado', 1), None]
        with patch.object(probe.subprocess, 'Popen', return_value=proc), \
             patch.object(probe.subprocess, 'run') as kill_tree, \
             patch.object(probe.os, 'killpg', create=True) as kill:
            result = probe.synthesize(self.env, str(self.binary), root, 'test', 1)
        if os.name == 'posix':
            kill.assert_called_once_with(proc.pid, probe.signal.SIGKILL)
        else:
            kill_tree.assert_called_once()
            proc.kill.assert_called_once()
        self.assertTrue(result['timeout'])
        self.assertFalse(result['passed'])

    def test_timeout_cannot_exceed_thirty_seconds(self):
        for value in ('31','inf','0','-1'):
            with self.assertRaises(ValueError):
                probe.ensure_license(dict(self.env, LICENSE_PROBE_TIMEOUT_S=value))


if __name__ == '__main__':
    unittest.main()
