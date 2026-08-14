def transform(inp):
    payload=inp.get("payload", inp)
    out={'status':payload['status']}
    return out
