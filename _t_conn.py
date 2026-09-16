import os, sqlite3, traceback
BASE=os.path.abspath(".")
DATA_DIR=os.path.join(BASE,"data")
print("DATA_DIR=",DATA_DIR,"exists=",os.path.isdir(DATA_DIR))
for name in ["his.db","iot.db","lis.db"]:
    p=os.path.join(DATA_DIR,name)
    print("---",name,"size=", os.path.getsize(p) if os.path.exists(p) else None, "attr-readonly?", (os.stat(p).st_mode & 0o200)==0 if os.path.exists(p) else "-")
try:
    con=sqlite3.connect(os.path.join(DATA_DIR,"his.db"))
    print("HIS WAL ->",con.execute("PRAGMA journal_mode=WAL").fetchone())
    print("patient rows:",list(con.execute("SELECT COUNT(*) FROM patient"))[0][0]); con.close()
except Exception:
    traceback.print_exc()
