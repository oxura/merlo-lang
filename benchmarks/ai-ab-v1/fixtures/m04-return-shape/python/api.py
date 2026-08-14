import json

def parse(inp):
    fields = {}
    for part in inp.strip().split("|"):
        key, value = part.split("=", 1)
        fields[key] = value
    return fields

def transform(inp):
    fields = parse(inp)
    error = fields.get("error", "legacy")
    return json.dumps(
        {"ok": False, "error": None if error == "none" else error},
        separators=(",", ":"),
    )
