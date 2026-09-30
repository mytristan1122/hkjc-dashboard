#!/usr/bin/env python3
"""Backfill HKJC official running-position records by meeting date.

Uses only the V19.1 runs_clean.csv to define the historical date/race scope and
join existing race metadata. Raw running positions are saved separately; this
script does not alter the model, app-new.py, or runs_clean.csv.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

BASE = "https://racing.hkjc.com/zh-hk/local/information/archive/displaysectionaltime"
DEFAULT_INDEX = Path("hkjc_quant/data/runs_clean.csv")
DEFAULT_OUTPUT = Path("hkjc_quant/data/running_positions_raw.csv")
FIELDS = ["race_id", "race_date", "venue", "race_no", "distance", "track",
          "track_config", "going", "horse_id", "horse_no", "horse_name",
          "finish_position", "call_positions_json", "call_details_json",
          "finish_time_sec", "source_url"]
VOID = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}

class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)
    def all(self, tag=None):
        out=[]
        for c in self.children:
            if isinstance(c, Node):
                if tag is None or c.tag == tag: out.append(c)
                out.extend(c.all(tag))
        return out
    def first(self, tag=None, cls=None):
        for n in self.all(tag):
            if cls is None or cls in n.attrs.get("class", "").split(): return n
        return None

class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root=Node("root"); self.stack=[self.root]
    def handle_starttag(self, tag, attrs):
        n=Node(tag,attrs); self.stack[-1].children.append(n)
        if tag not in VOID: self.stack.append(n)
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag,attrs); self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; return
    def handle_data(self, data):
        if data: self.stack[-1].children.append(data)

def txt(node): return re.sub(r"\s+", " ", node.text()).strip() if node else ""
def clean_id(href):
    q=parse_qs(urlparse(href or "").query)
    return q.get("horseid", [""])[0].strip()
def to_seconds(value):
    value=value.strip()
    m=re.fullmatch(r"(\d+):(\d{2})\.(\d+)",value)
    if m: return int(m[1])*60+int(m[2])+float("0."+m[3])
    try: return float(value)
    except ValueError: return ""

def parse_page(html, race_date, venue, source_url, metadata):
    p=TreeParser(); p.feed(html)
    race_nodes=[n for n in p.root.all("div") if re.fullmatch(r"Race\d+",n.attrs.get("id",""))]
    found={};
    for race in race_nodes:
        race_no=int(race.attrs["id"][4:])
        table=next((t for t in race.all("table") if "race_table" in t.attrs.get("class","").split()),None)
        if not table: continue
        rows=table.all("tr")
        records=[]
        for tr in rows[3:]:
            cells=[c for c in tr.children if isinstance(c,Node) and c.tag=="td"]
            if len(cells)<4: continue
            link=cells[2].first("a")
            horse_id=clean_id(link.attrs.get("href") if link else "")
            if not horse_id: continue
            calls=[]; positions=[]
            for idx,cell in enumerate(cells[3:9],1):
                pos_node=cell.first("span","f_fl")
                margin=cell.first("i")
                ps=cell.all("p")
                split_text=txt(ps[0]) if ps else ""
                pos=txt(pos_node) if pos_node else ""
                positions.append(pos or None)
                if pos:
                    calls.append({"call":idx,"position":pos,"margin":txt(margin),"sectional":split_text})
            base=metadata.get((race_no,horse_id),{})
            records.append({
                "race_id":base.get("race_id",f"{race_date.replace('-','')}_{venue}_{race_no}"),
                "race_date":race_date,"venue":venue,"race_no":race_no,
                "distance":base.get("distance",""),"track":base.get("track",""),
                "track_config":base.get("track_config",""),"going":base.get("going",""),
                "horse_id":horse_id,"horse_no":txt(cells[1]),"horse_name":txt(link),
                "finish_position":txt(cells[0]),
                "call_positions_json":json.dumps(positions,ensure_ascii=False),
                "call_details_json":json.dumps(calls,ensure_ascii=False),
                "finish_time_sec":to_seconds(txt(cells[9]) if len(cells)>9 else ""),
                "source_url":source_url})
        if records: found[race_no]=records
    return found

def load_index(path):
    meetings={}
    with path.open(encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        required={"race_date","venue","race_no","horse_id"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Index CSV needs columns: {', '.join(sorted(required))}")
        for r in reader:
            date=r["race_date"].strip(); venue=r["venue"].strip(); no=int(float(r["race_no"]))
            if not date or not venue: continue
            meeting=meetings.setdefault(date,{"venues":set(),"races":{}})
            meeting["venues"].add(venue)
            rr=meeting["races"].setdefault(no,{"horses":set(),"metadata":{}})
            hid=r["horse_id"].strip()
            if hid:
                rr["horses"].add(hid)
                rr["metadata"][hid]={"race_id":r.get("race_id", ""),"distance":r.get("distance", ""),
                    "track":r.get("track", ""),"track_config":r.get("track_config", ""),"going":r.get("going", "")}
    return meetings

def fetch(url, attempts=4):
    for attempt in range(attempts):
        req=Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; HKJC-Results-Archive/1.0)","Accept-Language":"zh-HK,zh;q=0.9,en;q=0.7"})
        try:
            with urlopen(req,timeout=30) as res:
                raw=res.read(); charset=res.headers.get_content_charset() or "utf-8"
                try: return raw.decode(charset)
                except UnicodeDecodeError: return raw.decode("utf-8","replace")
        except HTTPError as e:
            if e.code in (403,429): raise RuntimeError(f"HKJC returned HTTP {e.code}; stopped without retrying around the restriction")
            if e.code < 500 or attempt == attempts-1: raise
        except (URLError,TimeoutError) as e:
            if attempt == attempts-1: raise RuntimeError(f"request failed: {e}")
        time.sleep(min(2 ** attempt, 20))
    raise RuntimeError("request failed")

def read_existing(path):
    rows={}
    if path.exists():
        with path.open(encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f): rows[(r.get("race_id",""),r.get("horse_id",""))]=r
    return rows

def atomic_write(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader()
        for key in sorted(rows): w.writerow(rows[key])
    os.replace(tmp,path)

def main():
    ap=argparse.ArgumentParser(description="Backfill official HKJC race running positions; does not modify model files.")
    ap.add_argument("--index-csv",type=Path,default=DEFAULT_INDEX)
    ap.add_argument("--output",type=Path,default=DEFAULT_OUTPUT)
    ap.add_argument("--start-date",help="YYYY-MM-DD (optional)")
    ap.add_argument("--end-date",help="YYYY-MM-DD (optional)")
    ap.add_argument("--extra-meeting",action="append",default=[],metavar="DATE:VENUE",
                    help="Add a meeting absent from the model index, e.g. 2026-09-16:HV; repeat as needed")
    ap.add_argument("--max-meetings",type=int,default=0,help="0 means all matching meetings")
    ap.add_argument("--sleep",type=float,default=1.5,help="seconds between meeting pages")
    args=ap.parse_args()
    if not args.index_csv.exists():
        sys.exit(f"Model history CSV not found: {args.index_csv}. Upload the V19.1 hkjc_quant/data/runs_clean.csv first.")
    meetings=load_index(args.index_csv)
    for spec in args.extra_meeting:
        try:
            date,venue=spec.split(":",1)
            datetime.strptime(date,"%Y-%m-%d")
            venue=venue.strip().upper()
            if venue not in {"ST","HV"}: raise ValueError("venue must be ST or HV")
            if date in meetings and meetings[date]["venues"] != {venue}:
                raise ValueError(f"{date} already exists in index with a different venue")
            meetings.setdefault(date,{"venues":set(),"races":{}})["venues"].add(venue)
        except ValueError as e:
            sys.exit(f"Invalid --extra-meeting {spec!r}: {e}")
    dates=sorted(meetings)
    if args.start_date: dates=[d for d in dates if d>=args.start_date]
    if args.end_date: dates=[d for d in dates if d<=args.end_date]
    if args.max_meetings: dates=dates[:args.max_meetings]
    saved=read_existing(args.output); failures=[]
    print(f"Scope: {len(dates)} meeting dates from model CSV; output: {args.output}",flush=True)
    for ix,date in enumerate(dates,1):
        mt=meetings[date]
        if len(mt["venues"])!=1:
            failures.append((date,"multiple venues in model index; skipped")); print(f"[{ix}/{len(dates)}] {date}: skipped; multiple venues",flush=True); continue
        venue=next(iter(mt["venues"]))
        expected={(rno,hid) for rno,r in mt["races"].items() for hid in r["horses"]}
        if expected and all((mt["races"][rn]["metadata"][hid].get("race_id",f"{date.replace('-','')}_{venue}_{rn}"),hid) in saved for rn,hid in expected):
            print(f"[{ix}/{len(dates)}] {date}: already complete",flush=True); continue
        d=datetime.strptime(date,"%Y-%m-%d").strftime("%d/%m/%Y")
        url=BASE+"?"+urlencode({"All":"True","RaceDate":d})
        try:
            html=fetch(url); metadata={}
            for rn,r in mt["races"].items(): metadata.update({(rn,hid):m for hid,m in r["metadata"].items()})
            got=parse_page(html,date,venue,url,metadata)
            got_keys={(rn,row["horse_id"]) for rn,rows in got.items() for row in rows}
            missing=expected-got_keys; extra=got_keys-expected
            for rows in got.values():
                for row in rows: saved[(row["race_id"],row["horse_id"])]=row
            atomic_write(args.output,saved)
            if missing or extra:
                msg=f"race/runner mismatch: missing {len(missing)}, extra {len(extra)}"
                failures.append((date,msg)); print(f"[{ix}/{len(dates)}] {date}: saved {len(got_keys)} rows; WARNING {msg}",flush=True)
            else:
                print(f"[{ix}/{len(dates)}] {date}: verified {len(got)} races, {len(got_keys)} runners",flush=True)
        except Exception as e:
            failures.append((date,str(e))); print(f"[{ix}/{len(dates)}] {date}: FAILED {e}",flush=True)
            if "HTTP 403" in str(e) or "HTTP 429" in str(e): break
        time.sleep(max(0,args.sleep))
    if failures:
        fp=args.output.with_name("running_positions_failures.csv")
        with fp.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.writer(f); w.writerow(["race_date","reason"]); w.writerows(failures)
        print(f"Finished with {len(failures)} dates needing review: {fp}",flush=True)
    else: print("All selected meetings verified.",flush=True)
    print(f"Saved runner rows: {len(saved)}",flush=True)

if __name__=="__main__": main()
