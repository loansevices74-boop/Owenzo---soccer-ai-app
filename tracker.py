tracker.py — SQLite results tracker + ROI engine. No bookmaker odds anywhere.
"""
import re
import sqlite3
from datetime import datetime

DB_PATH = "soccer_tracker.db"


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS slips (
            slip_id INTEGER PRIMARY KEY AUTOINCREMENT,
            slip_type TEXT NOT NULL, created_at TEXT NOT NULL, settled_at TEXT,
            fixture_date TEXT NOT NULL, total_odds REAL NOT NULL,
            stake REAL NOT NULL, status TEXT DEFAULT 'PENDING',
            payout REAL DEFAULT 0, profit REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS legs (
            leg_id INTEGER PRIMARY KEY AUTOINCREMENT,
            slip_id INTEGER NOT NULL, country TEXT, league TEXT,
            home_team TEXT, away_team TEXT, match TEXT, market TEXT,
            selection TEXT, prob REAL, odds REAL, expected_score TEXT,
            actual_score TEXT, result TEXT DEFAULT 'PENDING',
            FOREIGN KEY(slip_id) REFERENCES slips(slip_id));""")


def log_slip(slip_type, legs, total_odds, stake, fixture_date):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO slips(slip_type, created_at, fixture_date, total_odds, stake)"
            " VALUES (?,?,?,?,?)",
            (slip_type, datetime.now().isoformat(), fixture_date, total_odds, stake))
        slip_id = cur.lastrowid
        for l in legs:
            c.execute(
                "INSERT INTO legs(slip_id, country, league, home_team, away_team,"
                " match, market, selection, prob, odds, expected_score)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (slip_id, l.get("country"), l.get("league"), l.get("home"),
                 l.get("away"), l["match"], l["market"], l["selection"],
                 l["prob"], l["odds"], l["expected_score"]))
    return slip_id


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _line(sel):
    m = re.search(r"(\d+(?:\.\d+)?)", sel)
    return float(m.group(1)) if m else None


def grade_market(market, selection, home, away, fh, fa, ht=None, corners=None):
    if market == "1X2":
        if selection == "Draw":
            return "WON" if fh == fa else "LOST"
        if selection.endswith("Win"):
            team = selection[:-3].strip()
            if _norm(team) == _norm(home):
                return "WON" if fh > fa else "LOST"
            if _norm(team) == _norm(away):
                return "WON" if fa > fh else "LOST"
        return "PENDING"
    if market.startswith("Over/Under"):
        line = _line(selection)
        if line is None:
            return "PENDING"
        tot = fh + fa
        if selection.startswith("Over"):
            return "WON" if tot > line else "LOST"
        if selection.startswith("Under"):
            return "WON" if tot < line else "LOST"
    if market == "BTTS":
        both = fh > 0 and fa > 0
        if selection.endswith("Yes"):
            return "WON" if both else "LOST"
        if selection.endswith("No"):
            return "WON" if not both else "LOST"
    if market == "HT Goals":
        if ht is None:
            return "PENDING"
        return "WON" if (ht[0] + ht[1]) > _line(selection) else "LOST"
    if market == "Corners":
        if corners is None:
            return "PENDING"
        return "WON" if corners > _line(selection) else "LOST"
    return "PENDING"


def enter_result(leg_id, fh, fa, ht=None, corners=None):
    with conn() as c:
        row = c.execute(
            "SELECT market, selection, home_team, away_team, slip_id"
            " FROM legs WHERE leg_id=?", (leg_id,)).fetchone()
        res = grade_market(row[0], row[1], row[2], row[3], fh, fa, ht, corners)
        c.execute("UPDATE legs SET result=?, actual_score=? WHERE leg_id=?",
                  (res, f"{fh}-{fa}", leg_id))
        slip_id = row[4]
    settle_slip(slip_id)
    return res


def settle_slip(slip_id):
    with conn() as c:
        slip = c.execute("SELECT stake FROM slips WHERE slip_id=?",
                         (slip_id,)).fetchone()
        legs = c.execute("SELECT odds, result FROM legs WHERE slip_id=?",
                         (slip_id,)).fetchall()
        if not slip or not legs or any(r[1] == "PENDING" for r in legs):
            return
        stake = slip[0]
        if any(r[1] == "LOST" for r in legs):
            status, payout = "LOST", 0.0
        else:
            prod = 1.0
            for od, res in legs:
                if res == "WON":
                    prod *= od
            status, payout = "WON", round(prod * stake, 2)
        c.execute("UPDATE slips SET status=?, payout=?, profit=ROUND(?-?,2),"
                  " settled_at=? WHERE slip_id=?",
                  (status, payout, payout, stake,
                   datetime.now().isoformat(), slip_id))


def auto_grade_from_feed(engine_module):
    pending = conn().execute(
        "SELECT l.leg_id, s.fixture_date, l.home_team, l.away_team,"
        " l.market, l.selection, l.slip_id"
        " FROM legs l JOIN slips s ON s.slip_id=l.slip_id"
        " WHERE l.result='PENDING'").fetchall()
    cache, graded = {}, 0
    for leg_id, d, home, away, market, selection, slip_id in pending:
        evs = cache.setdefault(d, engine_module.fetch_events_day(d))
        for e in evs:
            h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
            if not (_norm(h) and _norm(a)):
                continue
            if (_norm(h) == _norm(home) or _norm(home) in _norm(h)) and \
               (_norm(a) == _norm(away) or _norm(away) in _norm(a)):
                fh, fa = e.get("intHomeScore"), e.get("intAwayScore")
                if fh is None or fa is None:
                    break
                res = grade_market(market, selection, home, away, int(fh), int(fa))
                if res != "PENDING":
                    with conn() as c:
                        c.execute("UPDATE legs SET result=?, actual_score=?"
                                  " WHERE leg_id=?", (res, f"{fh}-{fa}", leg_id))
                    settle_slip(slip_id)
                    graded += 1
                break
    return graded


def pending_legs():
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(
            "SELECT l.leg_id, l.match, l.selection, l.market, s.slip_type"
            " FROM legs l JOIN slips s ON s.slip_id=l.slip_id"
            " WHERE l.result='PENDING' ORDER BY s.slip_id")]


def overall_stats():
    with conn() as c:
        rows = c.execute("SELECT stake, profit, status FROM slips").fetchall()
    staked = sum(r[0] for r in rows)
    profit = round(sum(r[1] for r in rows), 2)
    settled = [r for r in rows if r[2] in ("WON", "LOST")]
    won = sum(1 for r in settled if r[2] == "WON")
    return {"total_staked": round(staked, 2), "profit": profit,
            "roi": round(100.0 * profit / staked, 1) if staked else 0.0,
            "slips_settled": len(settled),
            "slip_hit_rate": round(100.0 * won / len(settled), 1) if settled else 0.0}


def group_hit_rate(field):
    assert field in ("country", "league", "market")
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(f"""
            SELECT {field} AS grp, COUNT(*) AS legs, SUM(result='WON') AS won,
                   ROUND(100.0*SUM(result='WON')/COUNT(*),1) AS hit_rate
            FROM legs WHERE result IN ('WON','LOST')
            GROUP BY {field} ORDER BY legs DESC""")]


def profit_curve():
    with conn() as c:
        return c.execute(
            "SELECT settled_at, profit FROM slips"
            " WHERE status IN ('WON','LOST') ORDER BY settled_at").fetchall()


def all_slips():
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(
            "SELECT slip_id, slip_type, fixture_date, total_odds, stake,"
            " status, payout, profit FROM slips ORDER BY slip_id DESC")]
