import json,sys
from service import run
inp=json.load(sys.stdin)
json.dump(run(inp),sys.stdout,sort_keys=True)
