"""监督器回归：已保存的代码必须能在 worker 硬超时后交付。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from serve.tasks.worker import execute


class WorkerTests(unittest.TestCase):
    def test_timeout_preserves_saved_code_and_releases_execution_slot(self):
        code = 'module TopModule; endmodule'
        script = (
            'import pathlib,sys,time; out=pathlib.Path(sys.argv[-1]); '
            f'(out/"solution.v").write_text({code!r}); '
            '(out/"trace.jsonl").write_text(\'{"tool":"candidate"}\\n\'); '
            'time.sleep(10)'
        )
        payload = dict(task_id='saved-before-timeout', nonce='local', mode='agent',
                       prompt='module named TopModule', interface='', deadline_s=2)
        with tempfile.TemporaryDirectory() as archive, \
             patch.dict(os.environ, {'RUN_ARCHIVE_DIR':archive}), \
             patch('serve.tasks.worker.worker_command', return_value=[sys.executable, '-c', script]):
            for mode in ('agent', 'baseline'):
                with self.subTest(mode=mode):
                    started = time.monotonic()
                    result = execute(dict(payload, mode=mode), started)
                    self.assertLess(time.monotonic()-started, 4)
                    self.assertEqual(result['solution'], code)
                    events = [json.loads(line) for line in result['trace'].splitlines()]
                    self.assertEqual(events[0]['tool'], 'candidate')
                    self.assertTrue(any(row.get('status') in ('timeout', 'worker_interrupted') for row in events))
            self.assertEqual(len(list(Path(archive).glob('request-*'))), 2)


if __name__ == '__main__':
    unittest.main()
