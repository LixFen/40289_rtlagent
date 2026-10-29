"""Assemble streamed model turns into readable and machine-readable traces."""
import json
from pathlib import Path
import re
from agent.llm.streaming import merge_delta


def assemble_sse(path):
    message, metadata, usage = {'role': 'assistant', 'content': ''}, {}, None
    reasoning, data = [], []
    finish, done, events = None, False, 0
    tool_calls = {}
    with Path(path).open('r', encoding='utf-8', errors='replace') as stream:
        for line in stream:
            line = line.rstrip('\r\n')
            if line.startswith('data:'):
                data.append(line[5:].removeprefix(' '))
            elif not line and data:
                payload, data = '\n'.join(data), []
                if payload == '[DONE]':
                    done = True
                    break
                try:
                    event = json.loads(payload)
                except ValueError:
                    break  # Keep the valid prefix of an interrupted event.
                if not isinstance(event, dict):
                    break
                events += 1
                if event.get('usage'):
                    usage = event['usage']
                metadata.update({k:v for k,v in event.items() if k not in ('choices', 'usage')})
                for choice in event.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    delta = choice.get('delta') or {}
                    if not isinstance(delta, dict):
                        continue
                    # Providers may switch aliases between events, or send both.
                    thought = delta.get('reasoning') or delta.get('reasoning_content') or ''
                    reasoning.append(thought if isinstance(thought, str) else '')
                    try:
                        merge_delta(message, delta, tool_calls)
                    except (KeyError, TypeError, ValueError):
                        # Preserve the valid prefix for diagnostic viewing only.
                        return {'response': dict(metadata, choices=[{'index':0, 'message':message, 'finish_reason':finish}], usage=usage),
                                'completed':False, 'stream_events':events, 'raw_file':Path(path).name}
                    if choice.get('finish_reason') is not None:
                        finish = choice['finish_reason']
    message['reasoning'] = ''.join(reasoning)
    return {'response': dict(metadata, choices=[{'index':0, 'message':message, 'finish_reason':finish}],
                             usage=usage),
            'completed': done and finish is not None, 'stream_events':events,
            'raw_file': Path(path).name}


def fenced(text, language='text'):
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    fence = '`' * max(3, 1 + max((len(s) for s in re.findall(r'`+', text)), default=0))
    return f'{fence}{language}\n{text}\n{fence}\n'


def export_trace(out, interrupted=False):
    out = Path(out)
    source = out / 'trace.jsonl'
    if not source.exists():
        return
    turns, records = {}, []
    for line in source.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            if line.strip():
                records.append({'tool':'worker', 'status':'incomplete_trace_record'})
            continue
        tool, call = row.get('tool'), row.get('call')
        if tool in ('llm_request', 'llm_stream_start', 'llm_stream_event', 'llm_stream_end', 'llm') and call is not None:
            if call not in turns:
                turns[call] = {'tool':'llm', 'call':call, 'ts':row.get('ts'), 'completed':False}
                records.append(turns[call])
            turn = turns[call]
            if tool == 'llm_request':
                turn['request'] = row['request']
            elif tool == 'llm':
                turn.update(row, completed=True)
            elif tool == 'llm_stream_end':
                turn.update(completed=row['completed'], stream_end=row)
            continue
        records.append(row)
    if interrupted:
        records.append({'tool':'budget', 'status':'timeout', 'source':'supervisor'})
    for call, turn in turns.items():
        raw = out / f'llm-{call:04d}.sse'
        if raw.exists():
            # Raw capture also contains complete events written just before a kill,
            # even when they never reached the event-log callback.
            assembled = assemble_sse(raw)
            response = turn.get('response', {}).copy()
            response.update(assembled.pop('response'))
            turn.update(assembled, response=response)
            if not turn['completed']:
                turn['status'] = 'interrupted' if interrupted else 'incomplete'
        if turn.get('completed'):
            turn['status'] = 'completed'
    markdown = ['# Agent trace\n', '模型分片已按轮拼接；未完成轮仅包含截止前收到的内容。\n']
    for row in records:
        if row.get('tool') == 'llm' and 'call' in row:
            message = row.get('response', {}).get('choices', [{}])[0].get('message', {})
            markdown.append(f"## 模型第 {row['call']} 轮 — {row.get('status', 'unknown')}\n")
            if 'sec' in row:
                markdown.append(f"耗时：{row['sec']:.3f} 秒\n")
            markdown.append('### 推理（按接收顺序拼接）\n')
            reasoning = message.get('reasoning') or message.get('reasoning_content') or ''
            markdown.append(fenced(reasoning) if reasoning else '未收到推理文本。\n')
            markdown.append('### 正文\n')
            markdown.append(fenced(message.get('content') or '') if message.get('content') else '未收到正文。\n')
            if message.get('tool_calls'):
                markdown.append('### 原生工具调用（参数分片已拼接）\n')
                markdown.append(fenced(json.dumps(message['tool_calls'], ensure_ascii=False, indent=2), 'json'))
            markdown.append('### 结束状态与用量\n')
            markdown.append(fenced(json.dumps({'completed':row.get('completed'),
                'finish_reason':row.get('response', {}).get('choices', [{}])[0].get('finish_reason'),
                'usage':row.get('response', {}).get('usage')}, ensure_ascii=False, indent=2), 'json'))
            if 'request' in row:
                markdown.append('### 本轮完整请求\n')
                markdown.append(fenced(json.dumps(row['request'], ensure_ascii=False, indent=2), 'json'))
        else:
            markdown.append(f"## {row.get('tool', 'event')}\n")
            markdown.append(fenced(json.dumps(row, ensure_ascii=False, indent=2), 'json'))
    for name, text in [('trace.merged.jsonl', ''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records)),
                       ('trace.md', '\n'.join(markdown))]:
        target = out / name
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(target)
    return records
