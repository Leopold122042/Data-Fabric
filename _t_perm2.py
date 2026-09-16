import os
def t(label,fn):
    try: fn(); print("OK  ",label)
    except Exception as e: print("FAIL",label,"->",type(e).__name__,e)
t("create new file _probe2.txt", lambda: (open("_probe2.txt","wb").write(b"x"), os.remove("_probe2.txt")))
def mod_existing():
    with open("app.py","r+b") as f:
        f.seek(0); b=f.read(1)  # read first to prove readable, then try write at end? just test seek+read; separate for write
t("open app.py r+b (no write yet)", mod_existing)
def w_existing():
    with open("_wprobe_existing.txt","wb") as f: pass
# overwrite an existing file via python by truncating a copy we own is not the point; test real medsim write-open without writing bytes:
t("open('medsim.py','rb')", lambda: (open("medsim.py","rb").read(4),))
