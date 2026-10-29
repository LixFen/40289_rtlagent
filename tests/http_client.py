"""仅供 HTTP 协议与服务集成回归使用的请求辅助代码。"""
import json
import time
import urllib.request


def solve_remote(url, payload, token, poll_s=2, timeout_s=5):
    """发送一次投题，随后按 job_id 查询；总等待包含排队和契约宽限。"""
    end = time.monotonic()+payload['deadline_s']+20
    headers = {'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    request = urllib.request.Request(url.rstrip('/')+'/v1/solve',data=json.dumps(payload).encode(),headers=headers)
    with urllib.request.urlopen(request,timeout=timeout_s) as response:
        if response.status != 202:
            raise ValueError('Submission must return HTTP 202')
        job = json.load(response)
    job_id = job['job_id']
    while True:
        if job.get('job_id') != job_id or job.get('task_id') != payload['task_id']:
            raise ValueError('Job identity mismatch')
        if job.get('status') == 'done':
            if not all(isinstance(job.get(key),str) for key in ('solution','trace')):
                raise ValueError('Malformed completed job')
            return job
        if job.get('status') not in ('queued','running'):
            raise ValueError('Unknown job status')
        left = end-time.monotonic()
        if left <= 0:
            raise TimeoutError('Job did not finish within deadline plus grace')
        time.sleep(min(poll_s,left))
        request = urllib.request.Request(url.rstrip('/')+'/v1/jobs/'+job_id,headers=headers)
        with urllib.request.urlopen(request,timeout=min(timeout_s,max(0.01,end-time.monotonic()))) as response:
            job = json.load(response)
