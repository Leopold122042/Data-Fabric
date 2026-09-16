import os,sqlite3,sys
try: sys.stdout.reconfigure(encoding="utf-8",errors="replace")
except Exception: pass
p=os.path.abspath(r"E:\数据编织\medfabric_run\data\pacs.db")
con=sqlite3.connect(p); print("tables:",[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]); con.close()
ph=os.path.abspath(r"E:\数据编织\medfabric_run\data\his.db"); c2=sqlite3.connect(ph); print("his tables:",[r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")]); c2.close()
