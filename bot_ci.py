# bot_ci.py v7 - uses engine_v7 (sharper, selective, value-tagged)
import os
import re
import math
import json
import sqlite3
import requests
from datetime import datetime, timedelta

import engine_v7 as E

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
BANKROLL = float(os.environ.get("BANKROLL", "1000"))
DB_PATH = "soccer_tracker.db"

BLOCKED_SOURCES = {"Simulated Reality League", "vFootball", "eFootball"}
OFFLINE_VERIFIED = [
    {"date": "2026-08-07", "league": "Veikkausliiga", "home": "SJK", "away": "Gnistan"},
    {"date": "2026-08-08", "league": "Leagues Cup", "home": "Inter Miami", "away": "CF Monterrey"},
    {"date": "2026-08-09", "league": "Leagues Cup", "home": "Seattle Sounders", "away": "Querétaro"},
    {"date": "2026-08-12", "league": "UEFA Super Cup", "home": "Paris Saint-Germain", "away": "Aston Villa"},
]


def fetch_events_day(date_str):
    for key in ["3", "123"]:
        try:
            r = requests.get("https://www.thesportsdb.com/api/v1/json/" + key + "/eventsday.php",
                             params={"d": date_str, "s": "Soccer"}, timeout=20)
            r.raise_for_status()
            return (r.json() or {}).get("events") or []
        except Exception:
            continue
    return []


def get_fixtures(days):
    out = []
    for i in range(days):
        d = (datetime.today().date() + timedelta(days=i)).strftime("%Y-%m-%d")
        evs = fetch_events_day(d)
        if not evs:
            evs = [dict(f) for f in OFFLINE_VERIFIED if f["date"] == d]
        for e in evs:
            c = E.league_country(e.get("strLeague"))
            if e.get("strHomeTeam") and e.get("strAwayTeam") and c not in BLOCKED_SOURCES:
                out.append({"league": e["strLeague"], "home": e["strHomeTeam"],
                            "away": e["strAwayTeam"]})
    return out


def build_accumulator(legs, max_odds, max_legs):
    legs = sorted(legs, key=lambda l: l["prob"], reverse=True)
    chosen, tot, seen = [], 1.0, set()
    for l in legs:
        if len(chosen) >= max_legs or l["match"] in seen:
            continue
        if tot * l["odds"] > max_odds:
            continue
        chosen.append(l)
        seen.add(l["match"])
        tot *= l["odds"]
    return chosen, round(tot, 2)


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS slips (slip_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_type TEXT NOT NULL, created_at TEXT NOT NULL, settled_at TEXT, fixture_date TEXT NOT NULL, total_odds REAL NOT NULL, stake REAL NOT NULL, status TEXT DEFAULT 'PENDING', payout REAL DEFAULT 0, profit REAL DEFAULT 0); CREATE TABLE IF NOT EXISTS legs (leg_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_id INTEGER NOT NULL, country TEXT, league TEXT, home_team TEXT, away_team TEXT, match TEXT, market TEXT, selection TEXT, prob REAL, odds REAL, expected_score TEXT, actual_score TEXT, result TEXT DEFAULT 'PENDING');")


def log_slip(slip_type, legs, tot, stake, fdate):
    with conn() as c:
        cur = c.execute("INSERT INTO slips(slip_type, created_at, fixture_date, total_odds, stake) VALUES (?,?,?,?,?)",
                        (slip_type, datetime.now().isoformat(), fdate, tot, stake))
        sid = cur.lastrowid
        for l in legs:
            c.execute("INSERT INTO legs(slip_id, country, league, home_team, away_team, match, market, selection, prob, odds, expected_score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, l.get("country"), l.get("league"), l.get("home"), l.get("away"),
                       l["match"], l["market"], l["selection"], l["prob"], l["odds"], l["expected_score"]))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def grade_market(market, sel, home, away, fh, fa):
    if market == "1X2":
        if sel == "Draw":
            return "WON" if fh == fa else "LOST"
        if sel.endswith("Win"):
            t = sel[:-3].strip()
            if _norm(t) == _norm(home):
                return "WON" if fh > fa else "LOST"
            if _norm(t) == _norm(away):
                return "WON" if fa > fh else "LOST"
        return "PENDING"
    if market.startswith("Over/Under"):
        m = re.search(r"(\d+(?:\.\d+)?)", sel)
        if not m:
            return "PENDING"
        line = float(m.group(1))
        tot = fh + fa
        if sel.startswith("Over"):
            return "WON" if tot > line else "LOST"
        if sel.startswith("Under"):
            return "WON" if tot < line else "LOST"
    if market == "BTTS":
        both = fh > 0 and fa > 0
        if sel.endswith("Yes"):
            return "WON" if both else "LOST"
        if sel.endswith("No"):
            return "WON" if not both else "LOST"
    return "PENDING"


def settle_slip(sid):
    with conn() as c:
        slip = c.execute("SELECT stake FROM slips WHERE slip_id=?", (sid,)).fetchone()
        legs = c.execute("SELECT odds, result FROM legs WHERE slip_id=?", (sid,)).fetchall()
        if not slip or not legs or any(r[1] == "PENDING" for r in legs):
            return
        stake = slip[0]
        if any(r[1] == "LOST" for r in legs):
            st_, pay = "LOST", 0.0
        else:
            prod = 1.0
            for od, res in legs:
                if res == "WON":
                    prod *= od
            st_, pay = "WON", round(prod * stake, 2)
        c.execute("UPDATE slips SET status=?, payout=?, profit=ROUND(?-?,2), settled_at=? WHERE slip_id=?",
                  (st_, pay, pay, stake, datetime.now().isoformat(), sid))


def auto_grade():
    pend = conn().execute("SELECT l.leg_id, s.fixture_date, l.home_team, l.away_team, l.market, l.selection, l.slip_id FROM legs l JOIN slips s ON s.slip_id=l.slip_id WHERE l.result='PENDING'").fetchall()
    cache = {}
    for leg_id, d, home, away, market, sel, sid in pend:
        evs = cache.setdefault(d, fetch_events_day(d))
        for e in evs:
            h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
            if _norm(h) and (_norm(h) == _norm(home) or _norm(home) in _norm(h)) and \
               (_norm(a) == _norm(away) or _norm(away) in _norm(a)):
                fh, fa = e.get("intHomeScore"), e.get("intAwayScore")
                if fh is None:
                    break
                res = grade_market(market, sel, home, away, int(fh), int(fa))
                if res != "PENDING":
                    with conn() as c:
                        c.execute("UPDATE legs SET result=?, actual_score=? WHERE leg_id=?", (res, str(fh) + "-" + str(fa), leg_id))
                    settle_slip(sid)
                break


def tg_send(text):
    if not TG_TOKEN:
        print("[DRY RUN]\n" + text)
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        print("TG failed:", e)


def send_long(text, limit=4000):
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        tg_send(text[:cut])
        text = text[cut + 1:]
    tg_send(text)


def fmt_slip(title, slip, tot, stake):
    L = [title, "Total AI odds: " + str(tot) + " | Stake: " + str(stake), "-" * 34, ""]
    for l in slip:
        L += [l.get("country", "-") + " | " + l["league"], l["match"],
              "Pick: " + l["market"] + " - " + l["selection"] + " @ " + str(l["odds"]) + " 💎VALUE",
              "Exp. score: " + l["expected_score"] + " | AI prob: " + str(int(l["prob"] * 100)) + "%", ""]
    L += ["-" * 34, "v7 engine: selective value legs only. Paper-trade first.",
          "", "📲 Join: https://t.me/owenzosoccerslips"]
    return "\n".join(L)


def clean_view(l):
    m, s = l["market"], l["selection"]
    if m == "Over/Under 2.5":
        return "high-scoring game expected" if s.startswith("Over") else "low-scoring game expected"
    if m == "BTTS":
        return "both teams likely to score" if s.endswith("Yes") else "a clean sheet likely"
    if m == "1X2":
        return "tight balance - draw possible" if s == "Draw" else s[:-3].strip() + " likely to win"
    return s


def fmt_clean(slip):
    L = ["🤖 AI MATCH ANALYSIS - " + str(datetime.today().date()),
         "Entertainment only | 18+", "-" * 30, ""]
    for l in slip:
        L += [l.get("country", "-") + " | " + l["league"], l["match"],
              "AI expected score: " + l["expected_score"], "AI view: " + clean_view(l), ""]
    L += ["-" * 30, "Full free analysis on Telegram 📲", "https://t.me/owenzosoccerslips"]
    return "\n".join(L)


def post_daily():
    legs = []
    for f in get_fixtures(1):
        legs += E.generate_legs(E.model_match(f["home"], f["away"], f["league"]))
    slip, tot = build_accumulator(legs, 8, 4)
    if not slip:
        tg_send("DAILY " + str(datetime.today().date()) + ": no value legs today. We wait. 🤖")
        return
    stake = round(BANKROLL * 0.005, 2)
    send_long(fmt_slip("DAILY VALUE SLIP (v7) - " + str(datetime.today().date()), slip, tot, stake))
    tg_send("🎬 CLEAN VERSION (screenshot for TikTok)\n\n" + fmt_clean(slip))
    log_slip("DAILY", slip, tot, stake, str(datetime.today().date()))


def post_results():
    auto_grade()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    rows = conn().execute("SELECT slip_type, fixture_date, total_odds, stake, status, profit FROM slips WHERE settled_at >= ? ORDER BY slip_id", (cutoff,)).fetchall()
    L = ["WEEKLY GRADED RESULTS - " + str(datetime.today().date()), "-" * 34]
    staked = ret = 0.0
    if not rows:
        L.append("No settled slips this week.")
    for t, d, odds, stake, status, profit in rows:
        staked += stake
        ret += stake + profit
        L.append(("WON " if status == "WON" else "LOST ") + t + " | " + d + " | odds " + str(odds) + " | P/L " + str(profit))
    profit = round(ret - staked, 2)
    L += ["-" * 34, "Staked: " + str(round(staked, 2)) + " | Profit: " + str(profit)
          + " | ROI: " + str(round(100 * profit / staked, 1) if staked else 0.0) + "%",
          "", "📲 Join: https://t.me/owenzosoccerslips"]
    send_long("\n".join(L))


init_db()
CRON = os.environ.get("CRON", "")
if CRON.endswith("* 0"):
    post_results()
else:
    post_daily()
