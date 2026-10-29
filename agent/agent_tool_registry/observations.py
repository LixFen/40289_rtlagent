"""Shared default projection; tools may override without changing raw evidence."""


def model_observation(observation):
    result = dict(observation)
    if isinstance(result.get('message'), str) and 'budget' in result['message'].lower():
        result['message'] = 'Operation could not be completed.'
    if 'runs' in result:
        result['runs'] = []
        for run in observation['runs']:
            item = {key:run[key] for key in ('tool','status','rc','log_path') if key in run}
            diagnostics = run.get('diagnostics', [])[:4]
            if diagnostics:
                item['diagnostics'] = diagnostics
            elif observation.get('status') != 'passed':
                excerpt = run.get('excerpt', '')
                item['excerpt'] = excerpt if len(excerpt)<=2000 else excerpt[:1000]+'\n...\n'+excerpt[-1000:]
            result['runs'].append(item)
    return result
