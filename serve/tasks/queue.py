"""异步投题：nonce 去重、单 worker 队列、状态查询和排队过期。"""
from collections import deque
import json
import threading
import time
import uuid


class JobManager:
    """将 HTTP 请求与耗时求解隔离，状态只在条件锁内更新。"""

    def __init__(self, runner):
        """启动求解线程及排队期限监控；runner 接收题目、投递时间和取消事件。"""
        self.runner = runner
        self.jobs, self.nonces, self.pending = {}, {}, deque()
        self.condition = threading.Condition()
        self.closed = False
        self.threads = [threading.Thread(target=target, daemon=True)
                        for target in (self._worker, self._expire_jobs)]
        for thread in self.threads:
            thread.start()

    def submit(self, payload, started):
        """原子去重并入队；首次提交立即获得 queued 快照。"""
        with self.condition:
            nonce = payload['nonce']
            if nonce in self.nonces:
                return self._public(self.jobs[self.nonces[nonce]])
            job_id = uuid.uuid4().hex
            job = {'job_id':job_id, 'task_id':payload['task_id'], 'status':'queued',
                   'payload':dict(payload), 'started':started, 'cancel':threading.Event()}
            self.jobs[job_id] = job
            self.nonces[nonce] = job_id
            self.pending.append(job_id)
            response = self._public(job)
            self.condition.notify_all()
            return response

    def get(self, job_id):
        """返回公开状态；只有 done 状态携带解题结果。"""
        with self.condition:
            job = self.jobs.get(job_id)
            return self._public(job) if job else None

    def _public(self, job):
        """构造契约字段，隐藏题面、内部路径和取消状态。"""
        result = {key:job[key] for key in ('job_id','task_id','status')}
        if job['status'] == 'done':
            result.update(job['result'])
        return result

    def _finish(self, job, result):
        """统一将正常、超时及异常求解转换为 done，不让任务卡在 running。"""
        job['result'] = {'solution':result.get('solution',''), 'trace':result.get('trace',''),
                         'elapsed_s':time.monotonic()-job['started']}
        job['status'] = 'done'
        self.condition.notify_all()

    def _empty_result(self, reason):
        """生成协议正常、代码为空的失败结果。"""
        return {'solution':'', 'trace':json.dumps({'ts':time.time(),'tool':'worker','status':reason})+'\n'}

    def _worker(self):
        """按投递顺序一次只运行一道题，客户端断开不会取消任务。"""
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.closed or self.pending)
                if self.closed:
                    return
                job = self.jobs[self.pending.popleft()]
                if job['status'] == 'done':
                    continue
                if time.monotonic()-job['started'] >= job['payload']['deadline_s']:
                    self._finish(job,self._empty_result('queue_timeout'))
                    continue
                job['status'] = 'running'
            try:
                result = self.runner(job['payload'],job['started'],job['cancel'])
                if not isinstance(result,dict) or not all(isinstance(result.get(key),str) for key in ('solution','trace')):
                    raise ValueError('Invalid worker result')
            except Exception as exc:
                result = self._empty_result(type(exc).__name__)
            with self.condition:
                if job['status'] != 'done':
                    self._finish(job,result)

    def _expire_jobs(self):
        """独立处理排队与运行期限，求解/回收异常也不会让任务一直停在 running。"""
        with self.condition:
            while not self.closed:
                now = time.monotonic()
                for job in self.jobs.values():
                    if job['status'] in ('queued','running') and now-job['started'] >= job['payload']['deadline_s']:
                        status = 'queue_timeout' if job['status']=='queued' else 'deadline_timeout'
                        job['cancel'].set()
                        self._finish(job,self._empty_result(status))
                self.condition.wait(timeout=0.05)

    def close(self):
        """取消本服务的在途任务，并唤醒、回收管理线程。"""
        with self.condition:
            self.closed = True
            for job in self.jobs.values():
                if job['status'] == 'running':
                    job['cancel'].set()
                elif job['status'] == 'queued':
                    self._finish(job,self._empty_result('service_stopped'))
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=20)
