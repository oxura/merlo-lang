#!/usr/bin/env python3
import argparse,json,subprocess
from pathlib import Path
CASES=[['2026-08-14', {'month': '2026-08'}], ['2026-08-14 extra', {'month': '2026-08'}], ['2026-08-14', {'month': '2026-08'}]]
def check(workspace,arm):
 root=Path(workspace)/arm; source=root/("main.py" if arm=="python" else "main.mlo"); results=[]
 for request,expected in CASES:
  try:
    proc=subprocess.run((["python3", "-B", str(source)] if arm=="python" else ["merlo","run",str(source)]),input=json.dumps(request, ensure_ascii=False).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
  except subprocess.TimeoutExpired:
    results.append({"passed":False,"terminal_reason":"timeout"}); continue
  try: actual=json.loads(proc.stdout)
  except (json.JSONDecodeError,UnicodeDecodeError): actual=None
  results.append({"passed":proc.returncode==0 and actual==expected,"actual":actual,"expected":expected})
 return {"case_id":"d10-date-bucket","passed":bool(results) and all(x["passed"] for x in results),"cases":results}
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("--workspace",required=True); p.add_argument("--arm",required=True,choices=["merlo","python"]); a=p.parse_args(); print(json.dumps(check(a.workspace,a.arm),sort_keys=True))
