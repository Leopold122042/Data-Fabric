raw = open("app.py", encoding="utf-8", newline="").read()
def norm(s): return s.replace("\r\n","\n").replace("\n","\r\n")

e4_lf = ('    return {"service_id": sid, "latency_ms": ms, '
         '"stats_after": {"calls": calls, "avg_ms": avg},\n'
         '            "error": err, "result": result}')
e5_lf = '    if auth["masked"]:\n        result = gov.mask_payload(result)'

for name, cand in [("E4", e4_lf), ("E5", e5_lf)]:
    print(name, "LFjoin_inFile=", raw.count(cand), "CRLFjoin_inFile=", raw.count(norm(cand)))

i = raw.find('stats_after')
print("AROUND_425:", repr(raw[i-10:i+85]))
j = raw.find('if auth["masked"]:')
print("AROUND_496:", repr(raw[j:j+75]))
k = raw.find('"security": auth, "circulation"')
print("AROUND_503:", repr(raw[k-20:k+80]))
