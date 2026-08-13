# euro_bot.py - European Leagues Early-Season Statistical Tracking & Predictive Engine
import os
import sqlite3
import requests
from datetime import datetime, timedelta

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_EURO_CHAT", "") or os.environ.get("TG_CHAT_ID", "")
DB = "euro.db"

TARGETS = [("premier league", "England"), ("la liga", "Spain"), ("serie a", "Italy"),
           ("allsvenskan", "Sweden"), ("eredivisie", "Netherlands"),
           ("premiership", "Scotland")]
BASE = {"England": 2.75, "Spain": 2.6, "Italy": 2.55, "Sweden": 2.95,
        "Netherlands": 3.1, "Scotland": 2.7}


def league_hit(league):
    l = (league or "").lower()
    for sub, c in TARGETS:
        if sub in l:
            return c
    return None


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, league TEXT, country TEXT, date TEXT, home TEXT, away TEXT, fh INTEGER, fa INTEGER, UNIQUE(league, date, home, away));")


def ingest():
    n = 0
    for i in range(1, 5):
        d = (datetime.today().date() - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            r = requests.get("https://www.thesportsdb.com/api/v1/json/3/eventsday.php",
                             params={"d": d, "s": "Soccer"}, timeout=20)
            evs = (r.json() or {}).get("events") or []
        except Exception:
            evs = []
        for e in evs:
            ctry = league_hit(e.get("strLeague"))
            fh, fa = e.get("intHomeScore"), e.get("intAwayScore")
            if ctry and fh is not None and fa is not None:
                with conn() as c:
                    cur = c.execute("INSERT OR IGNORE INTO results(league,country,date,home,away,fh,fa) VALUES (?,?,?,?,?,?,?)",
                                    (e["strLeague"], ctry, d, e["strHomeTeam"],
                                     e["strAwayTeam"], int(fh), int(fa)))
                    n += cur.rowcount > 0
    return n


def metrics(team, league):
    rows = conn().execute(
        "SELECT home, away, fh, fa FROM results WHERE league=? AND (home=? OR away=?) ORDER BY date",
        (league, team, team)).fetchall()
    g = len(rows)
    if g == 0:
        return None
    gf = ga = 0
    h_gf = h_ga = h_n = 0
    a_gf = a_ga = a_n = 0
    for r in rows:
        if r["home"] == team:
            gf += r["fh"]
            ga += r["fa"]
            h_gf += r["fh"]
            h_ga += r["fa"]
            h_n += 1
        else:
            gf += r["fa"]
            ga += r["fh"]
            a_gf += r["fa"]
            a_ga += r["fh"]
            a_n += 1
    return {"team": team, "g": g,
            "gf": round(gf / g, 2), "ga": round(ga / g, 2),
            "diff": round((gf - ga) / g, 2),
            "h_gf": round(h_gf / h_n, 2) if h_n else 0.0,
            "a_gf": round(a_gf / a_n, 2) if a_n else 0.0}


def cluster(m):
    if m["gf"] >= 1.5 or (m["gf"] >= 1.2 and m["ga"] >= 1.0):
        return "ATTACK"
    if m["ga"] <= 0.8 and m["gf"] <= 1.3:
        return "FORTRESS"
    return "NEUTRAL"


def mw_of(league):
    r = conn().execute(
        "SELECT MAX(g) AS mw FROM (SELECT home AS t, COUNT() AS g FROM results WHERE league=? GROUP BY home UNION ALL SELECT away, COUNT() FROM results WHERE league=? GROUP BY away)",
        (league, league)).fetchone()
    return (r["mw"] or 0) + 1


def matrices(hm, am):
    picks = []
    combined = (hm["gf"] + hm["ga"] + am["gf"] + am["ga"]) / 2.0
    if hm["g"] >= 2 and am["g"] >= 2:
        if combined >= 2.8 and hm["gf"] >= 1.2 and am["gf"] >= 1.2 and hm["ga"] >= 0.8 and am["ga"] >= 0.8:
            conf = 85 + (5 if combined >= 3.0 else 0) + (5 if hm["ga"] >= 1.0 and am["ga"] >= 1.0 else 0)
            picks.append(("OVER 1.5 GOALS", min(95, conf), combined))
        if combined >= 3.2 and hm["gf"] >= 1.5 and am["gf"] >= 1.5 and hm["ga"] >= 1.0 and am["ga"] >= 1.0:
            picks.append(("OVER 2.5 GOALS", min(90, 85 + (5 if combined >= 3.5 else 0)), combined))
        if abs(hm["diff"] - am["diff"]) <= 0.3 and hm["ga"] < 1.0 and am["ga"] < 1.0 and hm["gf"] <= 1.5 and am["gf"] <= 1.5:
            conf = 80 + int(9 * (1 - abs(hm["diff"] - am["diff"]) / 0.3))
            picks.append(("DRAW", conf, combined))
    return picks


def fetch_fixtures():
    out = []
    for i in range(0, 2):
        d = (datetime.today().date() + timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            r = requests.get("https://www.thesportsdb.com/api/v1/json/3/eventsday.php",
                             params={"d": d, "s": "Soccer"}, timeout=20)
            evs = (r.json() or {}).get("events") or []
        except Exception:
            evs = []
        for e in evs:
            ctry = league_hit(e.get("strLeague"))
            if ctry and e.get("strHomeTeam") and e.get("strAwayTeam"):
                out.append({"league": e["strLeague"], "ctry": ctry,
                            "home": e["strHomeTeam"], "away": e["strAwayTeam"]})
    return out


def tg_send(text):
    if not TG_TOKEN:
        print("[DRY RUN]\n" + text)
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text,
                            "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        print("TG failed:", e)


init()
added = ingest()

lines = ["⚽ EURO EARLY-SEASON EDGE - " + str(datetime.today().date()), "-" * 32]
seen = []
for lg in conn().execute("SELECT DISTINCT league, country FROM results").fetchall():
    seen.append(lg["league"] + " (MW" + str(mw_of(lg["league"])) + ")")
lines.append("Tracking: " + (", ".join(seen) if seen else "season opening - capturing MW1-2"))
lines.append("")

picks_found = 0
for f in fetch_fixtures():
    hm = metrics(f["home"], f["league"])
    am = metrics(f["away"], f["league"])
    if not hm or not am:
        continue
    for name, conf, combined in matrices(hm, am):
        picks_found += 1
        lines.append("🎯 " + name + " - " + str(conf) + "% confidence")
        lines.append(f["home"] + " vs " + f["away"] + " (" + f["league"] + ")")
        lines.append("Combined avg: " + str(round(combined, 1)) + " goals | xG proxy: "
                     + str(hm["gf"]) + "/" + str(am["gf"]) + " per game")
        lines.append("Clusters: " + cluster(hm) + " vs " + cluster(am))
        lines.append("")

if picks_found == 0:
    lines.append("No matrix qualifiers today - discipline over volume. 🤖")
    lines.append("")

att = conn().execute("SELECT home AS t, league FROM results").fetchall()
prof = {}
for r in att:
    m = metrics(r["t"], r["league"])
    if m and m["g"] >= 1:
        prof.setdefault((r["t"], r["league"]), m)
attacks = sorted([m for m in prof.values()], key=lambda m: m["gf"], reverse=True)[:6]
forts = sorted([m for m in prof.values()], key=lambda m: m["ga"])[:6]
lines.append("🔥 HIGH-OCTANE ATTACK CLUSTER:")
for m in attacks:
    lines.append("- " + m["team"] + " (" + str(m["gf"]) + " scored/g)")
lines.append("🛡️ DEFENSIVE FORTRESS CLUSTER:")
for m in forts:
    lines.append("- " + m["team"] + " (" + str(m["ga"]) + " conceded/g)")
lines.append("")
lines.append("Entertainment only | 18+ | Powered by Owenzo OS 🤖")
tg_send("\n".join(lines))
print("ingested %d new results, %d picks" % (added, picks_found))
