import os, re
for fn in sorted(os.listdir(".")):
    if not fn.endswith(".py"): continue
    try: txt=open(fn,encoding="utf-8").read()
    except Exception as e: print(fn,"ERR",e); continue
    for i,l in enumerate(txt.splitlines(),1):
        if "DATA_DIR" in l or re.search(r'["\']data["\']',l) or ".db" in l and ("join(" in l or "connect" in l or "_conn" in l or "dirname" in l):
            print("%s:%d: %s"%(fn,i,l.strip()))
