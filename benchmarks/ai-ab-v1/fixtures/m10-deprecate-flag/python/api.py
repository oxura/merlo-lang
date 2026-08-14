def transform(inp):
    payload=inp.get("payload", inp)
    out={'text':'Name: '+payload['name']}
    return out
