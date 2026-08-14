def transform(wire):
    operation, request_id = wire.split("|", 1)
    # Pre-migration interface call drops the request id.
    return {"status": "ok", "request_id": "missing"}
