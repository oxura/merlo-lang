def transform(inp):
    payload=inp.get("payload", inp)
    out={'seen':str(payload['value'])}
    return out
