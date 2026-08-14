def transform(inp):
    payload=inp.get("payload", inp)
    out={'ok':False,'error':payload.get('error','legacy')}
    return out
