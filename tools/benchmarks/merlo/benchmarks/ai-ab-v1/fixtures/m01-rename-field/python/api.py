import json

def parse(inp):
    fields = {}
    for part in inp.strip().split("|"):
        key, value = part.split("=", 1)
        fields[key] = value
    return fields

def transform(inp):
    fields = parse(inp)
    return json.dumps(
        {"id": int(fields["id"]), "display_name": fields["display_name"]},
        separators=(",", ":"),
    )
