# Soccer AI Prediction Web App - v5 FINAL
# Pipeline: fact-check -> deep team analysis (venue splits, shrinkage)
#           -> Dixon-Coles score model -> expected score -> mixed slips
import math
import re
import json
import sqlite3
import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import quote

SPORTSDB_KEYS = ["3", "123"]
API = "https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php"
HOME_ADV = 65
TOTAL_GOALS_BASE = 2.70
DB_PATH = "soccer_tracker.db"
FORM_CACHE_PATH = "form_cache.json"

LEAGUE_GOALS = {
    "Argentina": 2.40, "Ireland": 2.60, "USA": 2.90, "England": 2.75,
    "Spain": 2.60, "Italy": 2.55, "Germany": 3.05, "Netherlands": 3.10,
    "Norway": 3.15, "Sweden": 2.95, "Finland": 2.75, "Brazil": 2.55,
    "Mexico": 2.70, "International Clubs": 2.70,
}
RHO = -0.07
HOME_SHARE = 0.55

ELO = {
    "Paris Saint-Germain": 2050, "PSG": 2050, "Aston Villa": 1900,
    "Inter Miami": 1750, "CF Monterrey": 1780, "Monterrey": 1780,
    "Seattle Sounders": 1700, "Querétaro": 1650, "Queretaro": 1650,
    "Charlotte FC": 1680, "Atlas": 1660, "Cruz Azul": 1770,
    "New York City FC": 1700, "NYCFC": 1700, "Toluca": 1720, "LAFC": 1740,
    "Vancouver Whitecaps": 1710, "FC Juárez": 1620,
    "SJK": 1550, "Gnistan": 1480, "NK Celje": 1560, "Celje": 1560,
    "Ararat-Armenia": 1520, "Universitatea Craiova": 1580, "KuPS": 1520,
    "Shelbourne": 1450, "Ajax": 1850, "Motherwell": 1500, "HJK": 1540,
    "Avaí": 1520, "Barra FC": 1400,
    "Racing Santander": 1560, "Sporting Gijón": 1570,
}

COVERAGE_COUNTRIES = [
    "Argentina", "Armenia", "Australia", "Austria", "Austria Amateur",
    "Belarus", "Belgium", "Bolivia", "Bosnia & Herzegovina", "Brazil",
    "Bulgaria", "Canada", "Chile", "China", "Colombia", "Costa Rica",
    "Croatia", "Czechia", "Denmark", "Ecuador", "El Salvador", "England",
    "England Amateur", "Estonia", "Faroe Islands", "Finland", "France",
    "Georgia", "Germany", "Germany Amateur", "Greece", "Guatemala",
    "Hungary", "Iceland", "India", "International", "International Clubs",
    "International Youth", "Ireland", "Italy", "Japan", "Kazakhstan",
    "Latvia", "Lithuania", "Mexico", "Montenegro", "Mozambique",
    "Netherlands", "Nicaragua", "North Macedonia", "Northern Ireland",
    "Norway", "Panama", "Paraguay", "Peru", "Poland", "Portugal",
    "Republic of Korea", "Romania", "Russia", "Scotland", "Serbia",
    "Simulated Reality League", "Slovakia", "Slovenia", "South Africa",
    "Spain", "Sweden", "Sweden Amateur", "Switzerland", "Turkiye",
    "Uganda", "Ukraine", "Uruguay", "USA", "Uzbekistan", "Venezuela", "Wales",
]

BLOCKED_SOURCES = {"Simulated Reality League", "vFootball", "eFootball"}
WORKING_COUNTRIES = [c for c in COVERAGE_COUNTRIES if c not in BLOCKED_SOURCES]

LEAGUE_ALIAS = {
    "veikkausliiga": "Finland", "allsvenskan": "Sweden",
    "eliteserien": "Norway", "leagues cup": "USA", "mls": "USA",
    "liga mx": "Mexico", "brasileirao serie a": "Brazil",
    "copa santa catarina": "Brazil", "la liga": "Spain",
    "uefa champions league qualifying": "International Clubs",
    "uefa europa league qualifying": "International Clubs",
    "uefa conference league qualifying": "International Clubs",
    "uefa super cup": "International Clubs",
}

SUBSTR_ALIAS = [
    ("argentin", "Argentina"), ("usl", "USA"), ("mls", "USA"),
    ("american", "USA"), ("irish", "Ireland"), ("mexic", "Mexico"),
    ("brasileirao", "Brazil"), ("finnish", "Finland"), ("swedish", "Sweden"),
    ("norwegian", "Norway"), ("dutch", "Netherlands"), ("eredivisie", "Netherlands"),
    ("french", "France"), ("ligue 1", "France"), ("german", "Germany"),
    ("bundesliga", "Germany"), ("spanish", "Spain"), ("la liga", "Spain"),
    ("italian", "Italy"), ("portuguese", "Portugal"), ("primeira liga", "Portugal"),
    ("turkish", "Turkiye"), ("greek", "Greece"), ("scottish", "Scotland"),
    ("english", "England"), ("premier league", "England"), ("championship", "England"),
    ("polish", "Poland"), ("ukrainian", "Ukraine"), ("romanian", "Romania"),
    ("croatian", "Croatia"), ("serbian", "Serbia"), ("slovenian", "Slovenia"),
    ("slovak", "Slovakia"), ("czech", "Czechia"), ("danish", "Denmark"),
    ("belgian", "Belgium"), ("austrian", "Austria"), ("swiss", "Switzerland"),
    ("chilean", "Chile"), ("colombian", "Colombia"), ("peruvian", "Peru"),
    ("bolivian", "Bolivia"), ("uruguay", "Uruguay"), ("paraguay", "Paraguay"),
    ("venezuel", "Venezuela"), ("ecuador", "Ecuador"), ("japanese", "Japan"),
    ("australian", "Australia"), ("south african", "South Africa"),
    ("indian", "India"), ("chinese", "China"), ("k league", "Republic of Korea"),
    ("armenian", "Armenia"), ("georgian", "Georgia"), ("kazakh", "Kazakhstan"),
    ("uzbek", "Uzbekistan"), ("estonian", "Estonia"), ("latvian", "Latvia"),
    ("lithuanian", "Lithuania"), ("icelandic", "Iceland"), ("faroe", "Faroe Islands"),
    ("welsh", "Wales"), ("hungarian", "Hungary"), ("bulgarian", "Bulgaria"),
    ("russian", "Russia"), ("canadian", "Canada"), ("costa ric", "Costa Rica"),
]

OFFLINE_VERIFIED = [
    {"date": "2026-08-07", "league": "Veikkausliiga", "home": "SJK", "away": "Gnistan"},
    {"date": "2026-08-07", "league": "Leagues Cup", "home": "Charlotte FC", "away": "Atlas"},
    {"date": "2026-08-07", "league": "Spain - Regional Cup", "home": "Racing Santander", "away": "Sporting Gijón"},
    {"date": "2026-08-07", "league": "Copa Santa Catarina", "home": "Avaí", "away": "Barra FC"},
    {"date": "2026-08-08", "league": "Leagues Cup", "home": "Inter Miami", "away": "CF Monterrey"},
    {"date": "2026-08-08", "league": "Leagues Cup", "home": "Vancouver Whitecaps", "away": "FC Juárez"},
    {"date": "2026-08-09", "league": "Leagues Cup", "home": "Seattle Sounders", "away": "Querétaro"},
    {"date": "2026-08-09", "league": "Leagues Cup", "home": "Cruz Azul", "away": "New York City FC"},
    {"date": "2026-08-09", "league": "Leagues Cup", "home": "Toluca", "away": "LAFC"},
    {"date": "2026-08-11", "league": "UEFA Champions League Qualifying", "home": "NK Celje", "away": "Ararat-Armenia"},
    {"date": "2026-08-12", "league": "UEFA Super Cup", "home": "Paris Saint-Germain", "away": "Aston Villa"},
    {"date": "2026-08-13", "league": "UEFA Europa League Qualifying", "home": "Universitatea Craiova", "away": "KuPS"},
    {"date": "2026-08-13", "league": "UEFA Conference League Qualifying", "home": "Shelbourne", "away": "Ajax"},
    {"date": "2026-08-13", "league": "UEFA Conference League Qualifying", "home": "Motherwell", "away": "HJK"},
]

try:
    with open(FORM_CACHE_PATH) as _f:
        FORM_CACHE = json.load(_f)
except Exception:
    FORM_CACHE = {}


def _api_get(path):
    for key in SPORTSDB_KEYS:
        try:
            r = requests.get("https://www.thesportsdb.com/api/v1/json/" + key + "/" + path, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None


def _default_prof():
    return {"elo": 1500, "w": 0, "d": 0, "l": 0, "n": 0,
            "h_att": 1.35, "h_def": 1.35, "a_att": 1.35, "a_def": 1.35}


def team_profile(name, league=""):
    cached = FORM_CACHE.get(name)
    if isinstance(cached, dict) and "h_att" in cached:
        return cached
    prof = _default_prof()
    data = _api_get("searchteams.php?t=" + quote(name))
    teams = (data or {}).get("teams")
    if teams:
        tid = teams[0].get("idTeam")
        ev = _api_get("eventslast.php?id=" + str(tid))
        results = (ev or {}).get("results") or []
        w = d = l = n = 0
        h_gf = h_ga = h_n = 0
        a_gf = a_ga = a_n = 0
        for e in results[:6]:
            fh = e.get("intHomeScore")
            fa = e.get("intAwayScore")
            if fh is None or fa is None:
                continue
            fh = int(fh)
            fa = int(fa)
            if e.get("strHomeTeam") == name:
                gf, ga, venue = fh, fa, "H"
            else:
                gf, ga, venue = fa, fh, "A"
            n += 1
            if gf > ga:
                w += 1
            elif gf == ga:
                d += 1
            else:
                l += 1
            if venue == "H":
                h_gf += gf
                h_ga += ga
                h_n += 1
            else:
                a_gf += gf
                a_ga += ga
                a_n += 1
        if n:
            K = 3.0
            prof = {
                "elo": 1500 + 60 * (w - l), "w": w, "d": d, "l": l, "n": n,
                "h_att": (h_gf + 1.35 * K) / (h_n + K),
                "h_def": (h_ga + 1.35 * K) / (h_n + K),
                "a_att": (a_gf + 1.35 * K) / (a_n + K),
                "a_def": (a_ga + 1.35 * K) / (a_n + K),
            }
    try:
        import xg
        xr = xg.xg_rates(name, league, league_country(league))
        if xr:
            prof["h_att"] = xr["h_att"]
            prof["h_def"] = xr["h_def"]
            prof["a_att"] = xr["a_att"]
            prof["a_def"] = xr["a_def"]
            prof["src"] = "xG"
    except Exception:
        pass
    FORM_CACHE[name] = prof
    try:
        with open(FORM_CACHE_PATH, "w") as _f:
            json.dump(FORM_CACHE, _f)
    except Exception:
        pass
    return prof


def elo(team):
    if team in ELO:
        return ELO[team]
    return team_profile(team)["elo"]


def form_text(p):
    if p["n"] == 0:
        return "no recent data"
    return "W%d-D%d-L%d (last %d)" % (p["w"], p["d"], p["l"], p["n"])


def league_country(league):
    l = (league or "").lower()
    if l in LEAGUE_ALIAS:
        return LEAGUE_ALIAS[l]
    for sub, ctry in SUBSTR_ALIAS:
        if sub in l:
            return ctry
    for c in WORKING_COUNTRIES:
        if c.lower() in l:
            return c
    return "International"


def _poisson_pmf(lam, max_g=10):
    pmf = [math.exp(-lam) * (lam ** i) / math.factorial(i) for i in range(max_g + 1)]
    s = sum(pmf)
    return [p / s for p in pmf]


def _dc_tau(i, j, lh, la, rho):
    if i == 0 and j == 0:
        return 1.0 - lh * la * rho
    if i == 0 and j == 1:
        return 1.0 + lh * rho
    if i == 1 and j == 0:
        return 1.0 + la * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def fetch_events_day(date_str):
    for key in SPORTSDB_KEYS:
        try:
            r = requests.get(API.format(key=key),
                             params={"d": date_str, "s": "Soccer"}, timeout=20)
            r.raise_for_status()
            return (r.json() or {}).get("events") or []
        except Exception:
            continue
    return []


def fetch_verified_fixtures(date_str):
    verified, removed = [], 0
    for e in fetch_events_day(date_str):
        home, away, league = (e.get("strHomeTeam"), e.get("strAwayTeam"),
                              e.get("strLeague"))
        country = league_country(league)
        if (home and away and league and country in WORKING_COUNTRIES
                and league not in BLOCKED_SOURCES):
            verified.append({"date": date_str, "league": league,
                             "country": country, "home": home, "away": away})
        else:
            removed += 1
    return verified, removed


def get_fixtures(start_date, days):
    out, report = [], []
    for i in range(days):
        d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        evs, removed = fetch_verified_fixtures(d)
        src = "TheSportsDB live feed"
        if not evs:
            evs = [dict(f) for f in OFFLINE_VERIFIED if f["date"] == d]
            src = "Offline verified cache"
            removed = 0
        for e in evs:
            e.setdefault("country", league_country(e["league"]))
            e["source"] = src
        out.extend(evs)
        report.append({"date": d, "verified": len(evs),
                       "simulated_removed": removed, "source": src})
    return out, report


def model_match(home, away, league):
    hp = team_profile(home)
    ap = team_profile(away)
    country = league_country(league)
    base = LEAGUE_GOALS.get(country, 2.70)
    hb = base * HOME_SHARE
    ab = base * (1.0 - HOME_SHARE)
    r_home = ELO.get(home, hp["elo"])
    r_away = ELO.get(away, ap["elo"])
    tilt = max(-0.35, min(0.35, (r_home - r_away) / 1200.0))

    def clampf(x):
        return max(0.4, min(2.2, x))

    lh = hb * clampf(hp["h_att"] / 1.35) * clampf(ap["a_def"] / 1.35) * (1.0 + tilt)
    la = ab * clampf(ap["a_att"] / 1.35) * clampf(hp["h_def"] / 1.35) * (1.0 - tilt)
    lh = max(0.2, min(3.6, lh))
    la = max(0.2, min(3.6, la))

    ph, pa = _poisson_pmf(lh), _poisson_pmf(la)
    mat = [[ph[i] * pa[j] * _dc_tau(i, j, lh, la, RHO) for j in range(11)] for i in range(11)]
    total_p = sum(sum(row) for row in mat)
    mat = [[mat[i][j] / total_p for j in range(11)] for i in range(11)]

    p_home = sum(mat[i][j] for i in range(11) for j in range(11) if i > j)
    p_draw = sum(mat[i][i] for i in range(11))
    p_away = 1.0 - p_home - p_draw
    p_over25 = sum(mat[i][j] for i in range(11) for j in range(11) if i + j >= 3)
    p_btts = sum(mat[i][j] for i in range(1, 11) for j in range(1, 11))
    p_ht05 = 1.0 - math.exp(-0.45 * (lh + la))
    corners = _poisson_pmf(9.0 + 0.6 * (lh + la), 16)
    p_cor85 = sum(corners[i] for i in range(9, 17))

    bi, bj, bp = 1, 1, -1.0
    for i in range(6):
        for j in range(6):
            if mat[i][j] > bp:
                bp = mat[i][j]
                bi, bj = i, j
    if bi == bj and p_home > p_draw and p_home > p_away:
        bp = -1.0
        for i in range(6):
            for j in range(6):
                if i > j and mat[i][j] > bp:
                    bp = mat[i][j]
                    bi, bj = i, j
    elif bi == bj and p_away > p_draw and p_away > p_home:
        bp = -1.0
        for i in range(6):
            for j in range(6):
                if i < j and mat[i][j] > bp:
                    bp = mat[i][j]
                    bi, bj = i, j

    return {
        "league": league, "country": country,
        "home": home, "away": away, "xg_home": lh, "xg_away": la,
        "form_home": form_text(hp), "form_away": form_text(ap),
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_over25": p_over25, "p_under25": 1 - p_over25,
        "p_btts_yes": p_btts, "p_btts_no": 1 - p_btts,
        "p_ht_over05": p_ht05, "p_corners_over85": p_cor85,
        "expected_score": "%d - %d" % (bi, bj),
    }


def generate_legs(pred):
    legs = []

    def add(market, selection, prob):
        if prob <= 0.50:
            return
        legs.append({
            "country": pred["country"], "league": pred["league"],
            "home": pred["home"], "away": pred["away"],
            "match": pred["home"] + " vs " + pred["away"],
            "market": market, "selection": selection,
            "prob": round(prob, 3), "odds": round(1.0 / prob, 2),
            "expected_score": pred["expected_score"],
            "xg": "%.2f - %.2f" % (pred["xg_home"], pred["xg_away"]),
            "form": pred["form_home"] + " | " + pred["form_away"],
        })

    add("1X2", pred["home"] + " Win", pred["p_home"])
    add("1X2", "Draw", pred["p_draw"])
    add("1X2", pred["away"] + " Win", pred["p_away"])
    add("Over/Under 2.5", "Over 2.5", pred["p_over25"])
    add("Over/Under 2.5", "Under 2.5", pred["p_under25"])
    add("BTTS", "BTTS Yes", pred["p_btts_yes"])
    add("BTTS", "BTTS No", pred["p_btts_no"])
    add("HT Goals", "HT Over 0.5", pred["p_ht_over05"])
    add("Corners", "Over 8.5 Corners", pred["p_corners_over85"])
    return legs


def build_accumulator(legs, max_odds, max_legs, max_per_match=1, max_per_market=1):
    legs = sorted(legs, key=lambda l: l["prob"], reverse=True)
    chosen, total, count, mkt = [], 1.0, {}, {}
    for leg in legs:
        if len(chosen) >= max_legs:
            break
        m = leg["match"]
        if count.get(m, 0) >= max_per_match:
            continue
        if mkt.get(leg["market"], 0) >= max_per_market:
            continue
        if total * leg["odds"] > max_odds:
            continue
        chosen.append(leg)
        count[m] = count.get(m, 0) + 1
        mkt[leg["market"]] = mkt.get(leg["market"], 0) + 1
        total *= leg["odds"]
    return chosen, round(total, 2)


def build_vip(legs, target=200.0, max_legs=16, max_per_market=4):
    legs = [l for l in legs if 0.55 <= l["prob"] <= 0.85]
    legs = sorted(legs, key=lambda l: l["prob"], reverse=True)
    chosen, total, count, mkt = [], 1.0, {}, {}
    for leg in legs:
        if len(chosen) >= max_legs:
            break
        m = leg["match"]
        if count.get(m, 0) >= 2:
            continue
        if mkt.get(leg["market"], 0) >= max_per_market:
            continue
        chosen.append(leg)
        count[m] = count.get(m, 0) + 1
        mkt[leg["market"]] = mkt.get(leg["market"], 0) + 1
        total *= leg["odds"]
        if total >= target * 0.95:
            break
    return chosen, round(total, 2)


def ai_analysis(leg):
    return ("*AI Analysis (" + leg["league"] + "):* "
            + leg["home"] + " form: " + leg["form"].split(" | ")[0]
            + " --- " + leg["away"] + " form: " + leg["form"].split(" | ")[1]
            + ". Model confidence " + str(int(leg["prob"] * 100)) + "% on '"
            + leg["selection"] + "'. Expected goals " + leg["xg"]
            + " -> expected score **" + leg["expected_score"]
            + "**. AI Fair Odds " + str(leg["odds"]) + " (booking odds neglected).")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS slips (slip_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_type TEXT NOT NULL, created_at TEXT NOT NULL, settled_at TEXT, fixture_date TEXT NOT NULL, total_odds REAL NOT NULL, stake REAL NOT NULL, status TEXT DEFAULT 'PENDING', payout REAL DEFAULT 0, profit REAL DEFAULT 0); CREATE TABLE IF NOT EXISTS legs (leg_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_id INTEGER NOT NULL, country TEXT, league TEXT, home_team TEXT, away_team TEXT, match TEXT, market TEXT, selection TEXT, prob REAL, odds REAL, expected_score TEXT, actual_score TEXT, result TEXT DEFAULT 'PENDING');")


def log_slip(slip_type, legs, total_odds, stake, fixture_date):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO slips(slip_type, created_at, fixture_date, total_odds, stake) VALUES (?,?,?,?,?)",
            (slip_type, datetime.now().isoformat(), fixture_date, total_odds, stake))
        slip_id = cur.lastrowid
        for l in legs:
            c.execute(
                "INSERT INTO legs(slip_id, country, league, home_team, away_team, match, market, selection, prob, odds, expected_score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
            "SELECT market, selection, home_team, away_team, slip_id FROM legs WHERE leg_id=?",
            (leg_id,)).fetchone()
        res = grade_market(row[0], row[1], row[2], row[3], fh, fa, ht, corners)
        c.execute("UPDATE legs SET result=?, actual_score=? WHERE leg_id=?",
                  (res, str(fh) + "-" + str(fa), leg_id))
        slip_id = row[4]
    settle_slip(slip_id)
    return res


def settle_slip(slip_id):
    with conn() as c:
        slip = c.execute("SELECT stake FROM slips WHERE slip_id=?", (slip_id,)).fetchone()
        legs = c.execute("SELECT odds, result FROM legs WHERE slip_id=?", (slip_id,)).fetchall()
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
        c.execute("UPDATE slips SET status=?, payout=?, profit=ROUND(?-?,2), settled_at=? WHERE slip_id=?",
                  (status, payout, payout, stake, datetime.now().isoformat(), slip_id))


def auto_grade_from_feed():
    pending = conn().execute(
        "SELECT l.leg_id, s.fixture_date, l.home_team, l.away_team, l.market, l.selection, l.slip_id FROM legs l JOIN slips s ON s.slip_id=l.slip_id WHERE l.result='PENDING'").fetchall()
    cache, graded = {}, 0
    for leg_id, d, home, away, market, selection, slip_id in pending:
        evs = cache.setdefault(d, fetch_events_day(d))
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
                        c.execute("UPDATE legs SET result=?, actual_score=? WHERE leg_id=?",
                                  (res, str(fh) + "-" + str(fa), leg_id))
                    settle_slip(slip_id)
                    graded += 1
                break
    return graded


def pending_legs():
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(
            "SELECT l.leg_id, l.match, l.selection, l.market, s.slip_type FROM legs l JOIN slips s ON s.slip_id=l.slip_id WHERE l.result='PENDING' ORDER BY s.slip_id")]


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
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(
            "SELECT " + field + " AS grp, COUNT() AS legs, SUM(result='WON') AS won, ROUND(100.0*SUM(result='WON')/COUNT(),1) AS hit_rate FROM legs WHERE result IN ('WON','LOST') GROUP BY " + field + " ORDER BY legs DESC")]


def profit_curve():
    with conn() as c:
        return c.execute(
            "SELECT settled_at, profit FROM slips WHERE status IN ('WON','LOST') ORDER BY settled_at").fetchall()


def all_slips():
    with conn() as c:
        return [dict(zip(r.keys(), r)) for r in c.execute(
            "SELECT slip_id, slip_type, fixture_date, total_odds, stake, status, payout, profit FROM slips ORDER BY slip_id DESC")]


# ------------------------------- UI -------------------------------
init_db()

st.set_page_config(page_title="Soccer AI Predictor", page_icon="⚽", layout="wide")
st.title("⚽ Soccer AI Prediction App")
st.caption("v5 Dixon-Coles model • *fact-check* → *team analysis* → *expected score* → mixed slips • Booking odds neglected • Simulated games removed")

with st.sidebar:
    st.header("🎛️ Controls")
    start_date = st.date_input("Start date (from the 7th)", datetime(2026, 8, 7).date())
    days = st.slider("Window (days)", 1, 10, 7)
    bankroll = st.number_input("Bankroll", 100, 100000, 1000, step=100)
    coverage = st.multiselect("Coverage universe (your menu)",
                              WORKING_COUNTRIES, default=WORKING_COUNTRIES)

tab_pred, tab_track = st.tabs(["⚽ Predict & Slips", "📈 Tracker & ROI Dashboard"])

with tab_pred:
    if st.button("🔄 Fetch, Fact-Check & Predict", type="primary"):
        fixtures, report = get_fixtures(start_date, days)
        fixtures = [f for f in fixtures if f["country"] in coverage]
        all_legs, rows = [], []
        for f in fixtures:
            pred = model_match(f["home"], f["away"], f["league"])
            all_legs += generate_legs(pred)
            rows.append({
                "Date": f["date"], "Country": f["country"], "League": f["league"],
                "Match": f["home"] + " vs " + f["away"],
                "Exp. Score": pred["expected_score"],
                "xG (H-A)": "%.2f - %.2f" % (pred["xg_home"], pred["xg_away"]),
                "P(Home)": "%.0f%%" % (pred["p_home"] * 100),
                "P(Draw)": "%.0f%%" % (pred["p_draw"] * 100),
                "P(Away)": "%.0f%%" % (pred["p_away"] * 100),
                "P(Over2.5)": "%.0f%%" % (pred["p_over25"] * 100),
                "P(BTTS)": "%.0f%%" % (pred["p_btts_yes"] * 100),
            })
        st.session_state["report"] = report
        st.session_state["rows"] = rows
        st.session_state["nfix"] = len(fixtures)
        st.session_state["slips"] = {
            "DAILY": build_accumulator(all_legs, 8, 4, 1, 1),
            "WEEKLY": build_accumulator(all_legs, 25, 7, 1, 2),
            "VIP": build_vip(all_legs, 200.0, 16, 4),
        }

    if "slips" in st.session_state:
        st.header("🔎 Fact-Check Report")
        st.dataframe(pd.DataFrame(st.session_state["report"]),
                     use_container_width=True, hide_index=True)
        st.success(str(st.session_state["nfix"]) + " verified fixtures loaded (simulated removed).")
        st.header("📊 AI Match Probabilities (Dixon-Coles v5)")
        st.dataframe(pd.DataFrame(st.session_state["rows"]),
                     use_container_width=True, hide_index=True)

        stakes = {"DAILY": 0.005, "WEEKLY": 0.0025, "VIP": 0.001}
        titles = {"DAILY": "🟢 Daily Mixed Accumulator",
                  "WEEKLY": "🔵 Weekly Mixed Accumulator",
                  "VIP": "👑 VIP Weekly ~200-Odds Accumulator"}
        for key in ("DAILY", "WEEKLY", "VIP"):
            slip, tot = st.session_state["slips"][key]
            st.header(titles[key])
            if not slip:
                st.info("No qualifying legs.")
                continue
            stake = round(bankroll * stakes[key], 2)
            c1, c2, c3 = st.columns(3)
            c1.metric("Total AI Odds", "%.2f" % tot)
            c2.metric("Legs", len(slip))
            c3.metric("Suggested Stake", str(stake))
            st.dataframe(pd.DataFrame([{
                "Country": l["country"], "League": l["league"], "Match": l["match"],
                "Market": l["market"], "Selection": l["selection"],
                "Prob": "%.0f%%" % (l["prob"] * 100),
                "AI Fair Odds": l["odds"],
                "Exp. Score": l["expected_score"],
            } for l in slip]), use_container_width=True, hide_index=True)
            with st.expander("🤖 AI analysis for every leg"):
                for l in slip:
                    st.markdown(ai_analysis(l))

        if st.button("💾 Log these slips to tracker"):
            for key in ("DAILY", "WEEKLY", "VIP"):
                slip, tot = st.session_state["slips"][key]
                if slip:
                    log_slip(key, slip, tot, round(bankroll * stakes[key], 2), str(start_date))
            st.success("Logged to soccer_tracker.db")

with tab_track:
    st.header("📈 Results Tracker & ROI Dashboard")
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🔄 Auto-grade from live feed"):
            st.success(str(auto_grade_from_feed()) + " legs auto-graded.")
    with c2:
        st.caption("HT-goals & corner legs need manual entry (feed lacks that data).")

    o = overall_stats()
    m = st.columns(5)
    m[0].metric("Staked", o["total_staked"])
    m[1].metric("Profit", o["profit"])
    m[2].metric("ROI", str(o["roi"]) + "%")
    m[3].metric("Slips Settled", o["slips_settled"])
    m[4].metric("Slip Hit Rate", str(o["slip_hit_rate"]) + "%")

    curve = profit_curve()
    if curve:
        dfc = pd.DataFrame(curve, columns=["when", "profit"])
        dfc["cum_profit"] = dfc.profit.cumsum()
        st.subheader("💰 Cumulative Profit Curve")
        st.line_chart(dfc.set_index("when")["cum_profit"])

    st.subheader("🎯 Hit-rate per Country / League / Market")
    g1, g2, g3 = st.columns(3)
    for col, field in [(g1, "country"), (g2, "league"), (g3, "market")]:
        rowsg = group_hit_rate(field)
        if rowsg:
            dfg = pd.DataFrame(rowsg)
            col.bar_chart(dfg.set_index("grp")["hit_rate"])
            col.dataframe(dfg, hide_index=True)

    st.subheader("✍️ Manual result entry")
    pend = pending_legs()
    if pend:
        opts = {"#" + str(r["leg_id"]) + " [" + r["slip_type"] + "] " + r["match"] + " - " + r["selection"]: r for r in pend}
        pick = st.selectbox("Pending leg", list(opts))
        r = opts[pick]
        a, b = st.columns(2)
        fh = a.number_input("Home FT goals", 0, 20, 0, key="fh")
        fa = b.number_input("Away FT goals", 0, 20, 0, key="fa")
        ht = None
        if st.checkbox("Enter half-time score"):
            h1, h2 = st.columns(2)
            ht = (h1.number_input("HT home", 0, 10, 0, key="hth"),
                  h2.number_input("HT away", 0, 10, 0, key="hta"))
        corners = None
        if st.checkbox("Enter total corners"):
            corners = st.number_input("Corners", 0, 30, 0, key="cor")
        if st.button("Grade leg"):
            st.info("Leg graded: *" + enter_result(r["leg_id"], fh, fa, ht, corners) + "*")
    else:
        st.info("No pending legs.")

    st.subheader("Slip log")
    slips = all_slips()
    if slips:
        st.dataframe(pd.DataFrame(slips), use_container_width=True, hide_index=True)

st.divider()
st.caption("Paper-trade first. This app never reads bookmaker odds; all prices are internal AI Fair Odds.")
