from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from serve.api import SubmissionServer
from tests.http_client import solve_remote


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict('os.environ',{'FPGACHINA_TOKEN':'contract-test-token'})
        self.environment.start()
        self.release=threading.Event()
        self.calls=[]
        def runner(payload,started,cancel):
            self.calls.append(payload)
            if payload['prompt']=='slow':
                while not self.release.wait(0.01):
                    if cancel.is_set():return {'solution':'','trace':''}
            if payload['prompt']=='error':raise RuntimeError('test worker failed')
            if payload['prompt']=='invalid':return None
            return {'solution':'module TopModule; endmodule','trace':'{"tool":"llm"}\n'}
        self.server=SubmissionServer(('127.0.0.1',0),runner=runner)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.environment.stop()

    def payload(self,**updates):
        return dict(dict(task_id='task-8',nonce=uuid.uuid4().hex,mode='agent',prompt='quick',interface='',deadline_s=2),**updates)

    def request(self,path,payload=None,token='contract-test-token'):
        request=urllib.request.Request(self.url+path,data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=1) as response:
            return response.status,json.load(response)

    def done(self,job_id,timeout=1):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            _,body=self.request('/v1/jobs/'+job_id)
            if body['status']=='done':return body
            time.sleep(0.01)
        self.fail('Job did not reach done')

    def test_202_status_polling_and_completed_fields(self):
        started=time.monotonic()
        status,job=self.request('/v1/solve',self.payload(prompt='slow'))
        self.assertEqual(status,202)
        self.assertLess(time.monotonic()-started,0.5)
        self.assertEqual(job['status'],'queued')
        for _ in range(20):
            _,body=self.request('/v1/jobs/'+job['job_id'])
            self.assertEqual(set(body),{'job_id','task_id','status'})
            if body['status']=='running':break
            time.sleep(0.01)
        self.assertEqual(body['status'],'running')
        self.release.set()
        body=self.done(job['job_id'])
        self.assertEqual(body['task_id'],'task-8')
        self.assertEqual(set(body),{'job_id','task_id','status','solution','trace','elapsed_s'})
        self.assertIsInstance(body['elapsed_s'],float)
        json.loads(body['trace'])

    def test_concurrent_nonce_retry_solves_once(self):
        payload=self.payload(prompt='slow')
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs=list(pool.map(lambda _:self.request('/v1/solve',payload)[1],range(12)))
        self.assertEqual(len({j['job_id'] for j in jobs}),1)
        self.release.set()
        result=self.done(jobs[0]['job_id'])
        _,repeated=self.request('/v1/solve',dict(payload,task_id='different',prompt='quick'))
        self.assertEqual(repeated,result)
        self.assertEqual(len(self.calls),1)

    def test_queued_deadline_expires_while_first_job_is_running(self):
        _,first=self.request('/v1/solve',self.payload(prompt='slow'))
        _,queued=self.request('/v1/solve',self.payload(deadline_s=0.12))
        expired=self.done(queued['job_id'])
        self.assertEqual(expired['solution'],'')
        self.assertIn('queue_timeout',expired['trace'])
        self.assertGreaterEqual(expired['elapsed_s'],0.12)
        self.assertEqual(len(self.calls),1)
        self.release.set()
        self.done(first['job_id'])
        result=solve_remote(self.url,self.payload(),'contract-test-token',poll_s=0.01,timeout_s=1)
        self.assertTrue(result['solution'])

    def test_failures_finish_with_empty_result_and_service_recovers(self):
        for prompt in ('error','invalid'):
            result=solve_remote(self.url,self.payload(prompt=prompt),'contract-test-token',poll_s=0.01,timeout_s=1)
            self.assertEqual(result['status'],'done')
            self.assertEqual(result['solution'],'')
            json.loads(result['trace'])
        self.assertTrue(solve_remote(self.url,self.payload(),'contract-test-token',poll_s=0.01)['solution'])

    def test_auth_and_unknown_job(self):
        for path,payload in [('/v1/health',None),('/v1/solve',self.payload()),('/v1/jobs/no-such-job',None)]:
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(path,payload,token='wrong')
                self.assertEqual(error.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/jobs/no-such-job')
        self.assertEqual(error.exception.code,404)

    def test_shutdown_cancels_running_job(self):
        _,job=self.request('/v1/solve',self.payload(prompt='slow'))
        end=time.monotonic()+1
        while not self.calls and time.monotonic()<end:time.sleep(0.01)
        self.server.jobs.close()
        self.assertEqual(self.done(job['job_id'])['solution'],'')

    def test_running_deadline_reaches_done_independently(self):
        _,job=self.request('/v1/solve',self.payload(prompt='slow',deadline_s=0.12))
        result=self.done(job['job_id'])
        self.assertEqual(result['solution'],'')
        self.assertIn('deadline_timeout',result['trace'])

    def test_empty_nonce_is_still_idempotent(self):
        payload=self.payload(nonce='')
        _,first=self.request('/v1/solve',payload)
        _,second=self.request('/v1/solve',payload)
        self.assertEqual(first['job_id'],second['job_id'])
        self.done(first['job_id'])
        self.assertEqual(len(self.calls),1)
