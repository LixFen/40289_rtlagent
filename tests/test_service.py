"""Protocol/engineering tests using synthetic responses; no RTL scoring claims."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
import urllib.error
import urllib.request

from agent.tooling.checks import interface_errors
from serve.api import SubmissionServer
from serve.tasks.worker import execute
from tests.protocol import completion

PROMPT = "Implement a module named TopModule.\n - input a\n - output q\nWire a to q."
CODE = "module TopModule(input a, output q); assign q=a; endmodule"


class FakeModel(BaseHTTPRequestHandler):
    replies = []
    calls = []
    delay = 0

    def log_message(self, *_):
        pass

    def send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        self.send({"data": [{"id": "test-model"}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append(payload)
        if self.replies:
            content = self.replies.pop(0)
        elif payload.get('tools'):
            state = json.loads(payload['messages'][-1]['content'])
            if state['state']['current'] is None:
                action = {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': CODE}}
            elif state['last_observation'].get('path') == 'src/solution.sv':
                action = {'tool': 'check_interface'}
            else:
                action = {'tool': 'submit'}
            content = json.dumps(action)
        else:
            content = CODE
        time.sleep(self.delay)
        usage = {"prompt_tokens":10, "completion_tokens":12, "total_tokens":22}
        if payload.get('tools') and content is not None:
            self.send(completion(json.loads(content), f'call_{len(self.calls)}', usage))
        else:
            self.send({"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage":usage})


def background(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = background(ThreadingHTTPServer(("127.0.0.1", 0), FakeModel))
        cls.api = background(SubmissionServer(("127.0.0.1", 0)))
        cls.url = "http://127.0.0.1:" + str(cls.api.server_port)
        cls.archive = tempfile.TemporaryDirectory()
        cls.environment = patch.dict(os.environ, {
            "LLM_BASE_URL": "http://127.0.0.1:" + str(cls.model.server_port) + "/v1",
            "MODEL_NAME": "test-model", "FPGACHINA_TOKEN": "test-token",
            "RUN_ARCHIVE_DIR": cls.archive.name,
        })
        cls.environment.start()

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        for server in (cls.api, cls.model):
            server.shutdown()
            server.server_close()
        cls.archive.cleanup()

    def setUp(self):
        FakeModel.calls = []
        FakeModel.replies = []
        FakeModel.delay = 0

    def request(self, payload=None, token="test-token", path="/v1/solve"):
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            body=json.load(response)
            status=response.status
        if status != 202:
            return body
        end=time.monotonic()+10
        while body['status'] != 'done':
            if time.monotonic() >= end:
                raise TimeoutError('Test job did not finish')
            time.sleep(0.005)
            body=self.request(token=token,path='/v1/jobs/'+body['job_id'])
        return body

    def payload(self, **updates):
        return dict(dict(task_id="../../outside", nonce=uuid.uuid4().hex, mode="agent", prompt=PROMPT, interface="", deadline_s=5), **updates)

    def test_auth_both_endpoints(self):
        for payload, path in [(None, "/v1/health"), (self.payload(), "/v1/solve"),(None,'/v1/jobs/unknown')]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(payload, token="wrong", path=path)
            self.assertEqual(raised.exception.code, 401)
        self.assertEqual(FakeModel.calls, [])

    def test_health_without_telemetry_is_not_ready(self):
        with patch.dict(os.environ, {"GPU_TELEMETRY_FILE": "missing"}):
            body = self.request(path="/v1/health")
        self.assertFalse(body["ready"])
        self.assertIsNone(body["vram_gb"])
        self.assertEqual(body["track"], "rtl")

    def test_health_fresh_stale_and_over_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "gpu.json"
            with patch.dict(os.environ, {"GPU_TELEMETRY_FILE": str(file)}), \
                 patch('serve.health.declared_model',return_value='test-model'):
                for age, vram, ready in [(0, 20, True), (30, 20, False), (0, 33, False)]:
                    file.write_text(json.dumps(dict(ts=time.time()-age, vram_gb=vram, device_count=1)))
                    self.assertEqual(self.request(path="/v1/health")["ready"], ready)

    def test_health_rejects_mismatched_generation_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'gpu.json'
            file.write_text(json.dumps(dict(ts=time.time(),vram_gb=20,device_count=1)))
            with patch.dict(os.environ,{'GPU_TELEMETRY_FILE':str(file),'AGENT_REASONING_EFFORT':'xhigh'}), \
                 patch('serve.health.declared_model',return_value='test-model'), \
                 patch('serve.health.load_generation',return_value={'reasoning_effort':'low'}):
                self.assertFalse(self.request(path='/v1/health')['ready'])

    def test_invalid_requests(self):
        for payload in [[], {}, self.payload(mode="hls"), self.payload(deadline_s=True), self.payload(deadline_s=-1),
                        self.payload(deadline_s=float("nan")), self.payload(prompt=1)]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(payload)
            self.assertEqual(raised.exception.code, 400)

    def test_agent_and_path_isolation(self):
        body = self.request(self.payload())
        self.assertEqual(body["task_id"], "../../outside")
        self.assertIn("assign q=a", body["solution"])
        trace = [json.loads(line) for line in body["trace"].splitlines()]
        self.assertEqual([row["tool"] for row in trace].count("llm"), 3)
        checked = next(row for row in trace if row['tool'] == 'action' and row['decision']['tool'] == 'check_interface')
        self.assertEqual(checked['observation']['checked_ports'], 2)

    def test_baseline_is_one_call_without_skill(self):
        body = self.request(self.payload(mode="baseline"))
        self.assertIn("assign q=a", body["solution"])
        self.assertEqual(len(FakeModel.calls), 1)
        call = FakeModel.calls[0]
        self.assertEqual(call["messages"][1]["content"], PROMPT)
        self.assertNotIn("diagnostics", call["messages"][0]["content"])
        self.assertEqual((call["temperature"], call["top_p"], call["max_tokens"]), (0.0, 1.0, 16384))
        self.assertEqual([json.loads(line)["tool"] for line in body["trace"].splitlines()], ["baseline_meta", "llm"])

    def test_autonomous_http_preserves_full_run_without_reading_fixtures(self):
        FakeModel.replies = [json.dumps({'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': CODE}}),
                             json.dumps({'tool': 'check_interface'}), json.dumps({'tool': 'list_files'}),
                             json.dumps({'tool': 'submit'})]
        with tempfile.TemporaryDirectory() as archive:
            with patch.dict(os.environ, {'RUN_ARCHIVE_DIR': archive}):
                body = self.request(self.payload())
            runs = list(Path(archive).glob('request-*'))
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0]/'out/summary.json').exists())
            self.assertEqual((runs[0]/'out/solution.v').read_text(), CODE)
            self.assertEqual(sorted(p.name for p in (runs[0]/'task').iterdir()), ['interface.txt', 'prompt.txt'])
        self.assertEqual(body['solution'], CODE)
        self.assertEqual(len(FakeModel.calls), 4)
        self.assertEqual(json.loads(body['trace'].splitlines()[-1])['reason'], 'submitted')

    def test_interrupted_trace_keeps_solution_and_valid_records(self):
        class Worker:
            returncode = 0
            pid = 43210
            def __init__(self, args, **_):
                out = Path(args[-1])
                (out/'solution.v').write_text(CODE)
                (out/'trace.jsonl').write_text('{"tool":"candidate"}\n{"incomplete":')
            def wait(self, **_):
                return 0
        with patch('serve.tasks.worker.subprocess.Popen', side_effect=Worker), \
             patch('serve.tasks.worker.terminate'), patch('serve.tasks.worker.reap_group'):
            body = execute(self.payload(), time.monotonic())
        self.assertEqual(body['solution'], CODE)
        rows = [json.loads(line) for line in body['trace'].splitlines()]
        self.assertEqual(rows[-1]['status'], 'incomplete_trace_record')

    def test_agent_repairs_interface(self):
        FakeModel.replies = [json.dumps(action) for action in [
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': CODE.replace('output q', 'output z')}},
            {'tool': 'check_interface'},
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': CODE}},
            {'tool': 'check_interface'}, {'tool': 'submit'}]]
        body = self.request(self.payload())
        self.assertIn("output q", body["solution"])
        self.assertEqual(len(FakeModel.calls), 5)
        self.assertIn("Missing declared port q", FakeModel.calls[2]["messages"][-1]["content"])

    def test_agent_repairs_procedural_output_net(self):
        bad = "module TopModule(input clk,output [7:0] q);\nalways @(posedge clk) begin\nq <= 8'h01;\nend\nendmodule"
        good = bad.replace('output [7:0]', 'output reg [7:0]')
        FakeModel.replies = [json.dumps(action) for action in [
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': bad}},
            {'tool': 'check_interface'},
            {'tool': 'patch_file', 'args': {'path': 'src/solution.sv', 'old': 'output [7:0]', 'new': 'output reg [7:0]'}},
            {'tool': 'check_interface'}, {'tool': 'submit'}]]
        body = self.request(self.payload(prompt='module named TopModule\n - input clk\n - output q (8 bits)'))
        self.assertEqual(body['solution'].strip(), good)
        self.assertEqual(len(FakeModel.calls), 5)
        self.assertIn('must be declared reg or logic', FakeModel.calls[2]['messages'][-1]['content'])

    def test_null_content(self):
        FakeModel.replies = [None] * 3
        body = self.request(self.payload())
        self.assertEqual(body["solution"], "")
        self.assertLessEqual(len(FakeModel.calls), 3)

    def test_agent_does_not_return_file_access_source(self):
        FakeModel.replies = [json.dumps({'tool': 'write_file', 'args': {'path': 'src/solution.sv',
                             'content': CODE.replace('assign', '`include "outside.sv"\nassign')}}),
                             json.dumps({'tool': 'submit'})]
        self.assertEqual(self.request(self.payload())["solution"], "")

    def test_single_task_cli_stages_only_public_text(self):
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory) / "task", Path(directory) / "out"
            task.mkdir()
            (task / "prompt.txt").write_text(PROMPT)
            (task / "reference.sv").write_text("SECRET_REFERENCE_MARKER")
            proc = subprocess.run([sys.executable, "-m", "agent.cli", str(task), str(out)],
                                  capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("endmodule", (out / "solution.v").read_text())
            self.assertNotIn("SECRET_REFERENCE_MARKER", json.dumps(FakeModel.calls))

    def test_slow_model_is_bounded_and_service_recovers(self):
        for mode in ("agent", "baseline"):
            FakeModel.delay = 1
            start = time.monotonic()
            body = self.request(self.payload(mode=mode, deadline_s=0.25))
            self.assertLess(time.monotonic()-start, 0.9)
            self.assertEqual(body["solution"], "")
        FakeModel.delay = 0
        self.assertIn("module", self.request(self.payload())["solution"])

    def test_queue_time_counts_toward_budget(self):
        from serve.tasks.worker import SLOT
        SLOT.acquire()
        try:
            body = execute(self.payload(deadline_s=0.1), time.monotonic())
        finally:
            SLOT.release()
        self.assertEqual(body["solution"], "")
        self.assertIn("queue_timeout", body["trace"])

    def test_consecutive_requests(self):
        for index in range(200):
            body = self.request(self.payload(task_id=str(index), mode="baseline" if index % 2 else "agent"))
            self.assertEqual(body["task_id"], str(index))
            self.assertIn("endmodule", body["solution"])
            if index % 2 == 0:
                trace = [json.loads(line) for line in body['trace'].splitlines()]
                self.assertEqual(trace[0]['policy'], 'autonomous')


class CheckTests(unittest.TestCase):
    def test_observed_procedural_output_net_failure(self):
        bad = "module TopModule(input clk, output [7:0] q);\nalways @(posedge clk) begin\nq <= 8'h01;\nend\nendmodule"
        errors, _ = interface_errors('module named TopModule', '', bad)
        self.assertIn('Procedural output q must be declared reg or logic, not a wire net', errors)
        for fixed in (bad.replace('output [7:0]', 'output reg [7:0]'),
                      bad.replace('output [7:0]', 'output logic [7:0]'),
                      bad.replace('always', 'reg [7:0] q;\nalways')):
            self.assertEqual(interface_errors('module named TopModule', '', fixed)[0], [])

    def test_comparison_is_not_procedural_assignment(self):
        code = 'module TopModule(input [7:0] a,b, output q); assign q = (a <= b); endmodule'
        self.assertEqual(interface_errors('module named TopModule', '', code)[0], [])

    def test_grouped_ansi_and_nonansi(self):
        prompt = "module named TopModule\n - input a (8 bits)\n - input b (8 bits)\n - output q"
        for code in ["module TopModule(input [7:0] a,b, output logic q); endmodule",
                     "module TopModule(a,b,q); input [7:0] a,b; output q; endmodule"]:
            self.assertEqual(interface_errors(prompt, "", code), ([], 3))

    def test_wrong_width_and_direction(self):
        errors, _ = interface_errors("module named TopModule\n - input a (8 bits)", "", "module TopModule(output [3:0] a); endmodule")
        self.assertIn("Wrong direction or width for a", errors)

    def test_comment_cannot_satisfy_interface(self):
        errors, _ = interface_errors(PROMPT, "", "// module TopModule\nmodule Other(input a,output q); endmodule")
        self.assertIn("Expected module TopModule", errors)


if __name__ == "__main__":
    unittest.main()
