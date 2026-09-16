import re,sys
try: sys.stdout.reconfigure(encoding="utf-8",errors="replace")
except Exception: pass
t=open("governance.py",encoding="utf-8").read().splitlines()
for i,l in enumerate(t,1):
    if "ASSET" in l or any(k in l for k in ["clinical_patient","lab_result","imaging_index","vitals_stream","clinical_visit"]) and ("class" in l.lower() or ":" in l):
        print("%d: %s"%(i,l.rstrip()))
