import json,sys
from api import repair
inp=sys.stdin.read().strip()
json.dump(repair(inp),sys.stdout,sort_keys=True)
