def transform(inp):
    payload=inp.get("payload", inp)
    out={'timeout':payload.get('timeout',0)}
    return out
