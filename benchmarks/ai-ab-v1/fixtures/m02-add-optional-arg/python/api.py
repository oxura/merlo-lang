def transform(inp):
    payload=inp.get("payload", inp)
    out={'message':'Hello '+payload['name']}
    return out
