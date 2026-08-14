def transform(wire):
    operation, name = wire.split("|", 1)
    # Pre-migration module keeps the old greeting shape.
    return {"text": "Hello " + name}
