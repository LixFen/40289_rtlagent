import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.autonomous import solve
from agent.llm.protocol import parse_tool_actions, tool_definitions
from tests.protocol import completion
from agent.llm.streaming import read_stream
from agent.trace import assemble_sse, export_trace


def event(delta, finish=None):
    return ('data: '+json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}]},
                               ensure_ascii=False)+'\n\n').encode()


def stream(name, args, call_id='call_test', done=True):
    encoded = json.dumps(args, ensure_ascii=False)
    return (event({'tool_calls':[{'index':0,'id':call_id,'type':'function',
                    'function':{'name':name,'arguments':encoded[:5]}}]})
            + event({'tool_calls':[{'index':0,'function':{'arguments':encoded[5:]}}]}, 'tool_calls')
            + (b'data: [DONE]\n\n' if done else b''))


class NativeToolsTests(unittest.TestCase):
    def test_live_offline_and_readable_trace_preserve_split_arguments(self):
        args = {'content':'中文 "quote"\nmodule TopModule; endmodule','path':'src/solution.sv'}
        data = stream('write_file', args)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root/'llm-0001.sse'
            response, _ = read_stream(io.BytesIO(data),raw,lambda *_a,**_k:None,1,time.monotonic()+5,100000)
            offline = assemble_sse(raw)
            self.assertTrue(offline['completed'])
            self.assertEqual(response['choices'][0]['message']['tool_calls'],
                             offline['response']['choices'][0]['message']['tool_calls'])
            [(action, call)] = parse_tool_actions(response['choices'][0])
            self.assertEqual(action,{'tool':'write_file','args':args})
            self.assertNotIn('index',call)
            (root/'trace.jsonl').write_text(json.dumps({'tool':'llm_request','call':1,'request':{}})+'\n')
            export_trace(root)
            self.assertIn('原生工具调用',(root/'trace.md').read_text(encoding='utf-8'))
            self.assertIn('write_file',(root/'trace.md').read_text(encoding='utf-8'))

    def test_interleaved_calls_assemble_and_parse_all(self):
        data = (event({'tool_calls':[{'index':1,'id':'b','type':'function','function':{'name':'submit','arguments':'{'}}]})
                + event({'tool_calls':[{'index':0,'id':'a','type':'function','function':{'name':'list_files','arguments':'{}'}}]})
                + event({'tool_calls':[{'index':1,'function':{'arguments':'}'}}]},'tool_calls')+b'data: [DONE]\n\n')
        with tempfile.TemporaryDirectory() as directory:
            result,_ = read_stream(io.BytesIO(data),Path(directory)/'raw',lambda *_a,**_k:None,1,time.monotonic()+5,100000)
            self.assertEqual([c['id'] for c in result['choices'][0]['message']['tool_calls']],['a','b'])
            self.assertEqual([action['tool'] for action,_ in parse_tool_actions(result['choices'][0])],['list_files','submit'])

    def test_native_loop_pairs_ids(self):
        actions = [('write_file',{'path':'src/solution.sv','content':'module TopModule; endmodule'}),('submit',{})]
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            requests=[]
            def respond(request,**kwargs):
                body=json.loads(request.data)
                requests.append(body)
                self.assertNotIn('response_format',body)
                self.assertEqual(body['tool_choice'],'required')
                self.assertTrue(body['parallel_tool_calls'])
                name,args=actions.pop(0)
                return io.BytesIO(event({'content':'free_text_should_not_replay',
                                         'reasoning_content':'reasoning_should_not_replay'})+
                                  stream(name,args,'call_'+str(len(requests))))
            with patch.dict('os.environ',{'AGENT_STREAM':'1','DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen',side_effect=respond):
                solve(task,out)
            self.assertEqual(json.loads((out/'summary.json').read_text())['reason'],'submitted')
            messages=requests[1]['messages']
            self.assertEqual(messages[2]['role'],'assistant')
            self.assertIsNone(messages[2]['content'])
            self.assertNotIn('free_text_should_not_replay', json.dumps(requests[1]))
            self.assertEqual(messages[2]['reasoning_content'], 'reasoning_should_not_replay')
            self.assertEqual(messages[2]['tool_calls'][0]['id'],messages[3]['tool_call_id'])
            self.assertIn('source_content',json.loads(messages[3]['content']))
            rows = [json.loads(line) for line in (out/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            archived = next(row['response']['choices'][0]['message'] for row in rows if row['tool']=='llm')
            self.assertEqual(archived['content'], 'free_text_should_not_replay')
            self.assertEqual(archived['reasoning_content'], 'reasoning_should_not_replay')
            self.assertEqual(rows[0]['assistant_history'], 'tool_calls_and_reasoning')

    def test_missing_done_and_malformed_arguments_never_write(self):
        for data in (stream('write_file',{'path':'src/solution.sv','content':'module TopModule; endmodule'},done=False),
                     event({'tool_calls':[{'index':0,'id':'x','type':'function',
                            'function':{'name':'write_file','arguments':'{"path":'}}]},'tool_calls')+b'data: [DONE]\n\n'):
            with self.subTest(data=data),tempfile.TemporaryDirectory() as directory:
                task,out=Path(directory)/'task',Path(directory)/'out'
                task.mkdir()
                (task/'prompt.txt').write_text('Implement module TopModule.')
                with patch.dict('os.environ',{'AGENT_STREAM':'1','DEADLINE_S':'10'}), \
                     patch('agent.llm.client.urllib.request.urlopen',side_effect=lambda *_a,**_k:io.BytesIO(data)):
                    solve(task,out)
                self.assertEqual((out/'solution.v').read_text(),'')
                self.assertEqual(json.loads((out/'summary.json').read_text())['tool_actions'],0)

    def test_disabled_tool_not_advertised(self):
        self.assertNotIn('synth',[t['function']['name'] for t in tool_definitions({'synth'})])

    def test_batch_executes_in_order_and_returns_every_result(self):
        batches = [
            [{'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}},
             {'tool':'read_file','args':{'path':'src/solution.sv'}}],
            [{'tool':'read_file','args':{'path':'notes/missing.md'}}, {'tool':'list_files'}],
            [{'tool':'submit'}, {'tool':'write_file','args':{'path':'notes/late.md','content':'late'}}]]
        with tempfile.TemporaryDirectory() as directory:
            task,out=Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            requests=[]
            def respond(request,**_):
                requests.append(json.loads(request.data))
                response=completion(batches.pop(0),f'call_{len(requests)}')
                calls=response['choices'][0]['message']['tool_calls']
                return io.BytesIO(event({'tool_calls':[dict(call,index=i) for i,call in enumerate(calls)]},'tool_calls')+b'data: [DONE]\n\n')
            with patch.dict('os.environ',{'AGENT_STREAM':'1','DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen',side_effect=respond):
                solve(task,out)
            self.assertEqual(json.loads((out/'summary.json').read_text())['reason'],'submitted')
            self.assertFalse((out/'workspace/notes/late.md').exists())
            for request in requests[1:]:
                pending=[]
                for message in request['messages']:
                    if message['role']=='assistant':
                        self.assertFalse(pending)
                        pending=[call['id'] for call in message['tool_calls']]
                    elif message['role']=='tool':
                        self.assertEqual(message['tool_call_id'],pending.pop(0))
                self.assertFalse(pending)
            tool_results=[json.loads(m['content']) for m in requests[2]['messages'] if m['role']=='tool']
            self.assertTrue(any(r.get('content')=='module TopModule; endmodule' for r in tool_results))
            self.assertTrue(any(r.get('status')=='tool_error' for r in tool_results))
            rows=[json.loads(line) for line in (out/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            skipped=[r for r in rows if r['tool']=='action' and r['observation'].get('status')=='not_executed']
            self.assertEqual(len(skipped),1)

    def test_entire_batch_is_parsed_before_any_action(self):
        valid={'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}}
        for defect in ('duplicate_id','bad_arguments'):
            response=completion([valid,{'tool':'submit'}])
            calls=response['choices'][0]['message']['tool_calls']
            if defect=='duplicate_id':calls[1]['id']=calls[0]['id']
            else:calls[1]['function']['arguments']='{'
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                task,out=Path(directory)/'task',Path(directory)/'out'
                task.mkdir()
                (task/'prompt.txt').write_text('Implement module TopModule.')
                with patch.dict('os.environ',{'AGENT_STREAM':'0','DEADLINE_S':'10'}), \
                     patch('agent.llm.client.urllib.request.urlopen',side_effect=lambda *_a,**_k:io.BytesIO(json.dumps(response).encode())):
                    solve(task,out)
                self.assertEqual(json.loads((out/'summary.json').read_text())['tool_actions'],0)
                self.assertEqual((out/'solution.v').read_text(),'')

    def test_batch_history_pruning_keeps_complete_turns(self):
        batches=[
            [{'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}},
             {'tool':'read_file','args':{'path':'src/solution.sv'}}],
            [{'tool':'read_file','args':{'path':'src/solution.sv','offset':offset,'limit':10}}
             for offset in (0,10)],
            [{'tool':'read_file','args':{'path':'src/solution.sv','offset':offset,'limit':10}}
             for offset in (10,20)],
            [{'tool':'list_files'}, {'tool':'submit'}]]
        with tempfile.TemporaryDirectory() as directory:
            task,out=Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            requests=[]
            def respond(request,**_):
                body=json.loads(request.data)
                requests.append(body)
                if len(requests)==2:
                    import os
                    os.environ['AGENT_INPUT_CHAR_LIMIT']=str(len(json.dumps(body,ensure_ascii=False))+16)
                return io.BytesIO(json.dumps(completion(batches.pop(0),f'call_{len(requests)}')).encode())
            with patch.dict('os.environ',{'AGENT_STREAM':'0','DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen',side_effect=respond):
                solve(task,out)
            self.assertEqual(json.loads((out/'summary.json').read_text())['reason'],'submitted')
            self.assertLess(len(requests[2]['messages']),9)
            for request in requests:
                pending=[]
                for message in request['messages']:
                    if message['role']=='assistant':
                        self.assertFalse(pending)
                        pending=[call['id'] for call in message['tool_calls']]
                    elif message['role']=='tool':
                        self.assertEqual(message['tool_call_id'],pending.pop(0))
                self.assertFalse(pending)
