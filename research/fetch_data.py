#!/usr/bin/env python3
"""Fetch resolved sports moneyline markets + minute-level price histories.

Stage 1: market metadata per sport tag -> research/data/markets_<sport>.json
Stage 2: price history per market (token0, gameStart-12h .. closedTime, fidelity=1min)
         -> research/data/hist/<market_id>.json   (resumable, one file per market)
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from dateutil import parser as dtparser

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
HIST = os.path.join(DATA, "hist")
os.makedirs(HIST, exist_ok=True)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

SPORTS = {
    "nba": 745,
    "mlb": 100381,
    "nhl": 899,
    "nfl": 450,
    "epl": 306,
    "cbb": 101178,
    "tennis": 864,
    "laliga": 780,
    "bundesliga": 1494,
    "seriea": 100618,
    "ligue1": 102070,
    "ucl": 100977,
    "mls": 100100,
    "wnba": 100254,
}

session = requests.Session()


def fetch_markets(sport: str, tag_id: int) -> list:
    out_path = os.path.join(DATA, f"markets_{sport}.json")
    if os.path.exists(out_path):
        with open(out_path) as f:
            return json.load(f)
    rows = []
    # month windows to stay under Gamma's 10k offset cap
    windows = []
    y, mo = 2025, 1
    while (y, mo) <= (2026, 7):
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
                        "tag_id": tag_id, "sports_market_types": "moneyline",
                        "order": "endDate", "ascending": "true",
                        "end_date_min": dmin, "end_date_max": dmax,
                    }, timeout=60)
                    r.raise_for_status()
                    ms = r.json()
                    break
                except Exception as e:
                    print(f"  retry {sport} {dmin} offset {offset}: {e}")
                    time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"gave up fetching {sport} at {dmin} offset {offset}")
            if not ms:
                break
            pages.append(ms)
            offset += 100
            time.sleep(0.12)
    for ms in pages:
        for m in ms:
            if m.get("id") in seen_ids:
                continue
            seen_ids.add(m.get("id"))
            try:
                prices = json.loads(m.get("outcomePrices") or "[]")
                outcomes = json.loads(m.get("outcomes") or "[]")
                tokens = json.loads(m.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                continue
            if len(prices) != 2 or len(tokens) != 2 or len(outcomes) != 2:
                continue
            # only cleanly resolved 0/1 markets
            p0 = float(prices[0])
            if not (p0 >= 0.99 or p0 <= 0.01):
                continue
            if not m.get("gameStartTime") or not m.get("closedTime"):
                continue
            rows.append({
                "id": m["id"],
                "sport": sport,
                "question": m.get("question"),
                "slug": m.get("slug"),
                "outcomes": outcomes,
                "token0": tokens[0],
                "winner0": p0 >= 0.99,
                "gameStartTime": m["gameStartTime"],
                "closedTime": m["closedTime"],
                "endDate": m.get("endDate"),
                "volumeNum": float(m.get("volumeNum") or 0),
                "negRisk": m.get("negRisk"),
                "feesEnabled": m.get("feesEnabled"),
                "tick": m.get("orderPriceMinTickSize"),
            })
    with open(out_path, "w") as f:
        json.dump(rows, f)
    print(f"{sport}: {len(rows)} usable resolved moneyline markets")
    return rows


def fetch_history(m: dict) -> str:
    out_path = os.path.join(HIST, f"{m['id']}.json")
    if os.path.exists(out_path):
        return "cached"
    try:
        start = dtparser.parse(m["gameStartTime"])
        close = dtparser.parse(m["closedTime"])
    except (ValueError, TypeError):
        return "badtime"
    start_ts = int(start.timestamp()) - 12 * 3600
    end_ts = int(close.timestamp())
    if end_ts <= start_ts:
        return "badrange"
    # cap: ignore weird multi-day resolution lags beyond 12h after game start
    end_ts = min(end_ts, int(start.timestamp()) + 12 * 3600)
    for attempt in range(8):
        try:
            r = session.get(f"{CLOB}/prices-history", params={
                "market": m["token0"], "startTs": start_ts, "endTs": end_ts,
                "fidelity": 1,
            }, timeout=30)
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
    all_markets = []
    for sport, tag in SPORTS.items():
        all_markets.extend(fetch_markets(sport, tag))
    print(f"total markets: {len(all_markets)}")

    todo = [m for m in all_markets
            if not os.path.exists(os.path.join(HIST, f"{m['id']}.json"))]
    print(f"histories to fetch: {len(todo)}")
    counts = {}
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_history, m): m for m in todo}
        for fut in as_completed(futs):
            res = fut.result()
            counts[res] = counts.get(res, 0) + 1
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    print("history fetch done:", counts)


if __name__ == "__main__":
    main()
