def transform(inp):
    payload=inp.get("payload", inp)
    out=[int(x.strip()) for x in payload.split(',') if x.strip()][:-1]
    return out
