import json

def parse(inp):
    fields = {}
    for part in inp.strip().split("|"):
        key, value = part.split("=", 1)
        fields[key] = value
    return fields

def transform(inp):
    items = [item.strip() for item in parse(inp)["items"].split(",") if item.strip()]
    return json.dumps([int(item) for item in items[:-1]], separators=(",", ":"))
