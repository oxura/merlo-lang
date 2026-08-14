import json

def parse(inp):
    fields = {}
    for part in inp.strip().split("|"):
        key, value = part.split("=", 1)
        fields[key] = value
    return fields

def transform(inp):
    fields = parse(inp)
    timeout = int(fields.get("timeout", "0"))
    return json.dumps({"timeout": timeout}, separators=(",", ":"))
