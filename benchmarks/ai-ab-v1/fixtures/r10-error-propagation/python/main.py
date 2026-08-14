import json,sys
from api import repair
inp=json.load(sys.stdin)
json.dump(repair(inp),sys.stdout,sort_keys=True)
