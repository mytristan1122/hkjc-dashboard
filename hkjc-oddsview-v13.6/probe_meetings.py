#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe HKJC activeMeetings — 睇 HKJC 而家回俾我哋嘅賽期（date/venue/status）。
   放喺 hkjc-oddsview-v13.6/ 旁邊，用 app-new.py 入面同一條 query（byte-exact）。
   跑： .venv/bin/python3 probe_meetings.py
"""
import ast
import os
import sys
import json
import requests

API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}

# 由 app-new.py（或 app.py）抽出 TURNOVER_QUERY，唔使重抄成條 query
SRC = None
for cand in ("app-new.py", "app.py", "app-test.py"):
    if os.path.exists(cand):
        SRC = cand
        break
if not SRC:
    print("搵唔到 app-new.py / app.py，請喺 hkjc-oddsview-v13.6 入面跑")
    sys.exit(1)

tree = ast.parse(open(SRC, encoding="utf-8").read())
QUERY = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "TURNOVER_QUERY":
                QUERY = ast.literal_eval(node.value)
if not QUERY:
    print("喺 %s 搵唔到 TURNOVER_QUERY" % SRC)
    sys.exit(1)

print("用緊 query 來源：%s" % SRC)

# 先睇 DNS / 連線
try:
    payload = {"operationName": "raceMeetings",
               "variables": {"date": None, "venueCode": None},
               "query": QUERY}
    r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
except Exception as e:
    print("連線失敗（可能 DNS / 網絡）：%r" % e)
    sys.exit(2)

print("HTTP %s" % r.status_code)
try:
    j = r.json()
except Exception:
    print("回應唔係 JSON，頭 300 字：")
    print(r.text[:300])
    sys.exit(3)

if isinstance(j, dict) and j.get("errors"):
    print("GraphQL errors（可能 WHITELIST）：")
    print(json.dumps(j["errors"], ensure_ascii=False)[:500])

ams = ((j.get("data") or {}).get("activeMeetings") or [])
print("\nactiveMeetings 總數：%d" % len(ams))
print("-" * 44)
has_st = False
for m in ams:
    v = m.get("venueCode")
    if v == "ST":
        has_st = True
    races = m.get("races") or []
    print("date=%s  venue=%s  status=%s  races=%d"
          % ((m.get("date") or "")[:10], v, m.get("status"), len(races)))
print("-" * 44)
print("有冇沙田(ST)：%s" % ("有 ✅" if has_st else "冇 ❌（HKJC activeMeetings 而家唔包 ST）"))
