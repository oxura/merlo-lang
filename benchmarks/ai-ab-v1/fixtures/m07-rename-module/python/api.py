def transform(inp):
    payload=inp.get("payload", inp)
    out={'text':'Hello '+payload['name']}
    return out
