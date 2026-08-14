def transform(inp):
    payload=inp.get("payload", inp)
    out={'id':payload['id'],'display_name':payload['display_name']}
    return out
