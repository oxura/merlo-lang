#!/usr/bin/env python3
import argparse,json,subprocess
from pathlib import Path
CASES=[({'operation': 'migrate', 'payload': {'compact': True, 'name': 'Ada'}}, {'text': 'Ada'}), ({'operation': 'migrate', 'payload': {'compact': False, 'name': 'Ada'}}, {'text': 'Ada'}), ({'operation': 'migrate', 'payload': {'compact': False, 'name': 'Ada'}}, {'text': 'Ada'})]
UNTOUCHED=[({'operation': 'legacy', 'payload': {'compact': False, 'name': 'Ada'}}, {'text': 'Name: Ada'})]
def invoke(root,arm,request):
 source=root/("main.py" if arm=="python" else "main.mlo"); cmd=["python3", "-B", str(source)] if arm=="python" else ["merlo","run",str(source)]
 try:
  p=subprocess.run(cmd,input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
 except subprocess.TimeoutExpired:
  return False,None
 try: actual=json.loads(p.stdout)
 except (json.JSONDecodeError,UnicodeDecodeError): actual=None
 return p.returncode==0,actual
def check(workspace,arm):
 root=Path(workspace)/arm; migration=[invoke(root,arm,i)[1]==e for i,e in CASES]; untouched_ok=[invoke(root,arm,i)[1]==e for i,e in UNTOUCHED]
 return {"case_id":'m10-deprecate-flag',"passed":all(migration) and all(untouched_ok),"migration_passed":migration,"unaffected_passed":untouched_ok,"cases":len(CASES),"unaffected_cases":len(UNTOUCHED)}
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("--workspace",required=True); p.add_argument("--arm",required=True); a=p.parse_args(); print(json.dumps(check(a.workspace,a.arm)))
