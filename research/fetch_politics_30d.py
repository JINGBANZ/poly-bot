#!/usr/bin/env python3
"""Fetch last-30-days price history for top-volume politics markets.

API caps prices-history spans at 15 days, so fetch two chunks per market.
Output: research/data/phist/<id>.json (same format as other history files).
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dateutil import parser as dtparser

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PHIST = os.path.join(DATA, "phist")
os.makedirs(PHIST, exist_ok=True)
CLOB = "https://clob.polymarket.com"

TOP_N = 2000
CHUNK = 15 * 86400 - 60


def fetch_history(m):
    out_path = os.path.join(PHIST, f"{m['id']}.json")
    if os.path.exists(out_path):
        return "cached"
    try:
        end_ts = int(dtparser.parse(m["closedTime"]).timestamp())
        created = int(dtparser.parse(m["createdAt"]).timestamp())
    except (ValueError, TypeError):
        return "badtime"
    start_ts = max(created, end_ts - 2 * CHUNK)
    points = []
    s = requests.Session()
    t0 = start_ts
    while t0 < end_ts:
        t1 = min(t0 + CHUNK, end_ts)
        for attempt in range(6):
            try:
                r = s.get(f"{CLOB}/prices-history", params={
                    "market": m["token0"], "startTs": t0, "endTs": t1,
                    "fidelity": 180,
                }, timeout=30)
                if r.status_code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code == 400:
                    return "err400"
                r.raise_for_status()
                points.extend([[pt["t"], pt["p"]]
                               for pt in r.json().get("history", [])])
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            return "fail"
        t0 = t1
    with open(out_path, "w") as f:
        json.dump(points, f)
    return "ok"


def main():
    with open(os.path.join(DATA, "markets_politics.json")) as f:
        markets = json.load(f)
    markets.sort(key=lambda m: -m["volumeNum"])
    sel = markets[:TOP_N]
    print(f"fetching last-30d history for top {len(sel)} politics markets",
          flush=True)
    counts = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_history, m): m for m in sel}
        for fut in as_completed(futs):
            res = fut.result()
            counts[res] = counts.get(res, 0) + 1
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(sel)} {counts}", flush=True)
    print("done:", counts, flush=True)


if __name__ == "__main__":
    main()
