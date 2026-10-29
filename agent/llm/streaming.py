"""Incremental SSE capture; incomplete decisions are never executed."""
import json
from pathlib import Path
import time


def merge_delta(message, delta, tool_calls):
    """Accumulate function arguments by stream index, preserving first metadata."""
    for key, value in delta.items():
        if value is None:
            continue
        if key == 'tool_calls':
            for part in value:
                index = part['index']
                if not isinstance(index, int) or index < 0:
                    raise ValueError('Invalid tool call index')
                call = tool_calls.setdefault(index, {'function': {'arguments': ''}})
                for field in ('id', 'type'):
                    if part.get(field) is not None:
                        call[field] = part[field]
                function = part.get('function') or {}
                if function.get('name') is not None:
                    call['function']['name'] = function['name']
                arguments = function.get('arguments')
                if arguments is not None:
                    if not isinstance(arguments, str):
                        raise ValueError('Tool arguments delta must be text')
                    call['function']['arguments'] += arguments
            message['tool_calls'] = [tool_calls[i] for i in sorted(tool_calls)]
        elif isinstance(value, str) and key != 'role':
            message[key] = message.get(key, '') + value
        else:
            message[key] = value


def read_stream(response, raw_path, record, call, end, byte_limit):
    size, sequence = 0, 0
    message, usage, metadata = {'role': 'assistant', 'content': ''}, {}, {}
    finish, completed = None, False
    data = []
    tool_calls = {}
    record('llm_stream_start', call=call, raw_file=str(Path(raw_path).name))
    try:
        with Path(raw_path).open('wb') as raw:
            while True:
                if time.monotonic() >= end:
                    raise TimeoutError('Model stream deadline reached')
                line = response.readline(min(65536, byte_limit - size + 1))
                if not line:
                    raise ValueError('Model stream ended without [DONE]')
                raw.write(line)
                raw.flush()
                size += len(line)
                if size > byte_limit:
                    raise ValueError('Model stream exceeds byte budget')
                # readline(size) may split a large SSE line. Accumulate until newline.
                while not line.endswith(b'\n'):
                    part = response.readline(min(65536, byte_limit - size + 1))
                    if not part:
                        raise ValueError('Incomplete SSE line')
                    raw.write(part)
                    raw.flush()
                    size += len(part)
                    if size > byte_limit:
                        raise ValueError('Model stream exceeds byte budget')
                    if time.monotonic() >= end:
                        raise TimeoutError('Model stream deadline reached')
                    line += part
                text = line.decode('utf-8').rstrip('\r\n')
                if text.startswith('data:'):
                    data.append(text[5:].removeprefix(' '))
                elif not text and data:
                    payload = '\n'.join(data)
                    data = []
                    sequence += 1
                    if payload == '[DONE]':
                        record('llm_stream_event', call=call, sequence=sequence, done=True)
                        if finish is None:
                            raise ValueError('Model stream has no finish_reason')
                        completed = True
                        break
                    event = json.loads(payload)
                    record('llm_stream_event', call=call, sequence=sequence, event=event)
                    if 'error' in event:
                        raise ValueError('Model stream reported an upstream error; see stream event')
                    if event.get('usage'):
                        usage = event['usage']
                    for key, value in event.items():
                        if key not in ('choices', 'usage'):
                            metadata[key] = value
                    for choice in event.get('choices', []):
                        if choice.get('index', 0) != 0:
                            continue
                        merge_delta(message, choice.get('delta') or {}, tool_calls)
                        if choice.get('finish_reason') is not None:
                            finish = choice['finish_reason']
        result = dict(metadata, choices=[{'index': 0, 'message': message, 'finish_reason': finish}], usage=usage)
        return result, size
    finally:
        record('llm_stream_end', call=call, completed=completed, wire_bytes=size,
               content_chars=len(message.get('content', '')),
               reasoning_chars=len(message.get('reasoning', '')) + len(message.get('reasoning_content', '')),
               finish_reason=finish, usage=usage or None)
