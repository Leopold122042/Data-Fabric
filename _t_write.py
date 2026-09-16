import os, traceback
DATA_DIR=os.path.abspath("data")
print("target:", DATA_DIR)
# 1) try plain open-create of a new file inside data/
for fn in ["_probe_new.txt","his.db-wal"]:
    p=os.path.join(DATA_DIR,fn)
    if os.path.exists(p): 
        print(fn,"already exists; skip create")
    else:
        try:
            with open(p,"wb") as f: f.write(b"x")
            print("CREATE OK:", fn)
            os.remove(p); print("  (removed probe)")
        except Exception as e:
            print("CREATE FAIL:",fn,type(e).__name__,e)
# 2) try to remove an existing sidecar explicitly with python
for fn in ["iot.db-shm","lis.db-wal"]:
    p=os.path.join(DATA_DIR,fn)
    if os.path.exists(p):
        try: os.remove(p); print("REMOVED:",fn)
        except Exception as e: print("REMOVE FAIL:",fn,type(e).__name__,e)
# 3) can we rename his.db (needs write on dir)?
p=os.path.join(DATA_DIR,"his.db"); q=os.path.join(DATA_DIR,"_his_renamed.tmp")
try: os.rename(p,q); os.rename(q,p); print("RENAME OK (dir writable)")
except Exception as e: print("RENAME FAIL:",type(e).__name__,e)
