def repair(inp):
    # Generic operation protocol: ok:<integer> or err:<code>.
    kind, separator, payload = inp.partition(':')
    if kind == 'ok' and separator:
        return {'ok': True, 'value': int(payload), 'error': None}
    if kind == 'err' and separator:
        # Baseline defect: the error is swallowed instead of propagated.
        return {'ok': True, 'value': None, 'error': None}
    return {'ok': False, 'value': None, 'error': 'invalid'}
