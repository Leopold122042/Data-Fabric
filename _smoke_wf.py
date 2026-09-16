# -*- coding: utf-8 -*-
import json, urllib.request as u
B = "http://127.0.0.1:8321"
def get(p): return json.load(u.urlopen(B+p, timeout=15))
def post(p, body=None):
    data = None if body is None else json.dumps(body).encode()
    r = u.Request(B+p, data=data, method="POST", headers={"Content-Type":"application/json"})
    return json.load(u.urlopen(r, timeout=20))

print("HEALTH:", get("/api/health"))
c = get("/api/circulation/recent?limit=6")
print("CIRC metrics:", c.get("metrics"), " recent_n=", len(c.get("recent", [])))
for x in c.get("recent", [])[:4]:
    print("   rec:", x.get("ts"), x.get("service_id"), x.get("flow_id"), x.get("decision"), "masked=" + str(x.get("masked")), x.get("latency_ms"), "ms")

p360 = post("/api/data-services/SVC-PATIENT-360/invoke",
            {"actor": {"id":"researcher-b01","role":"researcher","org":"B院"}, "purpose":"research","cross_org":True,
             "params":{"patient_id":"P0042"}})
wf = p360.get("waterfall") or []
print("\nPATIENT-360 governed: error=", p360.get("error"), " masked_sec=", (p360.get("security") or {}).get("masked"))
print("  waterfall sources:", [(w.get("source"), w.get("ms")) for w in wf])

fed = post("/api/services/SVC-FED-QUERY/invoke", {"group_by":"dept","top_n":5})
fwf = fed.get("waterfall") or []
print("\nFED-QUERY legacy: error=", fed.get("error"), " total_ms=", (fed.get("result") or {}).get("federation_total_ms"))
print("  waterfall sources:", [(w.get("source"), w.get("ms")) for w in fwf])

c2 = get("/api/circulation/recent?limit=3")
print("\nCIRC after invokes metrics:", c2.get("metrics"))
