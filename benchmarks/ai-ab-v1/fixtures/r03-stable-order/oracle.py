#!/usr/bin/env python3
import argparse,json,subprocess
from pathlib import Path
CASES=[("1:b,1:a", "1:b,1:a"), ("1:a,1:a", "1:a,1:a"), ("1:a,2:b,3:c", "1:a,2:b,3:c")]
def check(workspace,arm):
 root=Path(workspace)/arm; source=root/("main.py" if arm=="python" else "main.mlo"); results=[]
 for n,(request,expected) in enumerate(CASES):
  cmd=["python3", "-B", str(source)] if arm=="python" else ["merlo","run",str(source)]
  try:
   p=subprocess.run(cmd,input=request.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
  except subprocess.TimeoutExpired:
   results.append({"case_id":n,"passed":False,"actual":"NOT_EXECUTED","expected":expected,"error":"timeout"})
   continue
  try: actual=json.loads(p.stdout)
  except (json.JSONDecodeError,UnicodeDecodeError): actual=None
  results.append({"case_id":n,"passed":p.returncode==0 and actual==expected,"actual":actual,"expected":expected})
 return {"case_id":"r03-stable-order","passed":all(r["passed"] for r in results),"defect_case_passed":results[0]["passed"],"unaffected_cases_passed":all(r["passed"] for r in results[1:]),"cases":results}
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("--workspace",required=True); p.add_argument("--arm",required=True); a=p.parse_args(); print(json.dumps(check(a.workspace,a.arm)))
