#!/usr/bin/env python3
"""Fetch resolved politics markets + 2h-fidelity price histories (full life)."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dateutil import parser as dtparser

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PHIST = os.path.join(DATA, "phist")
os.makedirs(PHIST, exist_ok=True)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
session = requests.Session()


def fetch_markets():
    out_path = os.path.join(DATA, "markets_politics.json")
    if os.path.exists(out_path):
        with open(out_path) as f:
            return json.load(f)
    rows = []
    windows = []
    y, mo = 2024, 9
    while (y, mo) <= (2028, 12):
        nxt_y, nxt_mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        windows.append((f"{y}-{mo:02d}-01T00:00:00Z",
                        f"{nxt_y}-{nxt_mo:02d}-01T00:00:00Z"))
        y, mo = nxt_y, nxt_mo
    seen_ids = set()
    pages = []
    for dmin, dmax in windows:
        offset = 0
        while True:
            for attempt in range(8):
                try:
                    r = session.get(f"{GAMMA}/markets", params={
                        "closed": "true", "limit": 100, "offset": offset,
                        "tag_id": 2, "volume_num_min": 10000,
                        "end_date_min": dmin, "end_date_max": dmax,
                        "order": "id", "ascending": "true",
                    }, timeout=60)
                    r.raise_for_status()
                    ms = r.json()
                    break
                except Exception as e:
                    print(f"retry {dmin} offset {offset}: {e}")
                    time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"gave up at {dmin} offset {offset}")
            if not ms:
                break
            pages.append(ms)
            offset += 100
            time.sleep(0.1)
    for ms in pages:
        for m in ms:
            if m.get("id") in seen_ids:
                continue
            seen_ids.add(m.get("id"))
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                tokens = json.loads(m.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                continue
            if len(prices) != 2 or len(tokens) != 2:
                continue
            p0 = float(prices[0])
            if not (p0 >= 0.99 or p0 <= 0.01):
                continue
            if not m.get("closedTime") or not m.get("createdAt"):
                continue
            ev = (m.get("events") or [{}])
            rows.append({
                "id": m["id"],
                "question": m.get("question"),
                "slug": m.get("slug"),
                "eventSlug": ev[0].get("slug", "") if ev else "",
                "token0": tokens[0],
                "winner0": p0 >= 0.99,
                "createdAt": m["createdAt"],
                "closedTime": m["closedTime"],
                "endDate": m.get("endDate"),
                "volumeNum": float(m.get("volumeNum") or 0),
                "negRisk": m.get("negRisk"),
            })
    with open(out_path, "w") as f:
        json.dump(rows, f)
    print(f"politics: {len(rows)} usable resolved markets")
    return rows


def fetch_history(m):
    out_path = os.path.join(PHIST, f"{m['id']}.json")
    if os.path.exists(out_path):
        return "cached"
    try:
        start_ts = int(dtparser.parse(m["createdAt"]).timestamp())
        end_ts = int(dtparser.parse(m["closedTime"]).timestamp())
    except (ValueError, TypeError):
        return "badtime"
    if end_ts <= start_ts:
        return "badrange"
    for attempt in range(8):
        try:
            r = session.get(f"{CLOB}/prices-history", params={
                "market": m["token0"], "startTs": start_ts, "endTs": end_ts,
                "fidelity": 120,
            }, timeout=60)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            h = r.json().get("history", [])
            with open(out_path, "w") as f:
                json.dump([[pt["t"], pt["p"]] for pt in h], f)
            return "ok"
        except Exception:
            time.sleep(2 * (attempt + 1))
    return "fail"


def main():
    markets = fetch_markets()
    todo = [m for m in markets
            if not os.path.exists(os.path.join(PHIST, f"{m['id']}.json"))]
    print(f"histories to fetch: {len(todo)}")
    counts = {}
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_history, m): m for m in todo}
        for fut in as_completed(futs):
            res = fut.result()
            counts[res] = counts.get(res, 0) + 1
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    print("done:", counts)


if __name__ == "__main__":
    main()
