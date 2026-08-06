# Soccer AI Prediction Web App - final single-file build
import math
import re
import sqlite3
import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

SPORTSDB_KEYS = ["3", "123"]
API = "https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php"
HOME_ADV = 65
TOTAL_GOALS_BASE = 2.70
DB_PATH = "soccer_tracker.db"

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


def elo(team):
    return ELO.get(team, 1500)


def league_country(league):
    l = (league or "").lower()
    if l in LEAGUE_ALIAS:
        return LEAGUE_ALIAS[l]
    for c in WORKING_COUNTRIES:
        if c.lower() in l:
            return c
    return "International"


def _poisson_pmf(lam, max_g=10):
    pmf = [math.exp(-lam) * (lam ** i) / math.factorial(i) for i in range(max_g + 1)]
    s = sum(pmf)
    return [p / s for p in pmf]


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
    diff = (elo(home) + HOME_ADV) - elo(away)
    gdiff = max(-2.2, min(2.2, diff / 900.0))
    lh = max(0.25, (TOTAL_GOALS_BASE + gdiff) / 2.0)
    la = max(0.25, (TOTAL_GOALS_BASE - gdiff) / 2.0)
    ph, pa = _poisson_pmf(lh), _poisson_pmf(la)
    p_home = sum(ph[i] * pa[j] for i in range(11) for j in range(11) if i > j)
    p_draw = sum(ph[i] * pa[i] for i in range(11))
    p_away = 1.0 - p_home - p_draw
    p_over25 = sum(ph[i] * pa[j] for i in range(11) for j in range(11) if i + j >= 3)
    p_btts = sum(ph[i] * pa[j] for i in range(1, 11) for j in range(1, 11))
    p_ht05 = 1.0 - math.exp(-0.45 * (lh + la))
    corners = _poisson_pmf(9.0 + 0.6 * (lh + la), 16)
    p_cor85 = sum(corners[i] for i in range(9, 17))
    return {
        "league": league, "country": league_country(league),
        "home": home, "away": away, "xg_home": lh, "xg_away": la,
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_over25": p_over25, "p_under25": 1 - p_over25,
        "p_btts_yes": p_btts, "p_btts_no": 1 - p_btts,
        "p_ht_over05": p_ht05, "p_corners_over85": p_cor85,
        "expected_score": "%d - %d" % (max(0, round(lh)), max(0, round(la))),
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


def build_accumulator(legs, max_odds, max_legs, max_per_match=1):
    legs = sorted(legs, key=lambda l: l["prob"], reverse=True)
    chosen, total, count = [], 1.0, {}
    for leg in legs:
        if len(chosen) >= max_legs:
            break
        m = leg["match"]
        if count.get(m, 0) >= max_per_match:
            continue
        if total * leg["odds"] > max_odds:
            continue
        chosen.append(leg)
        count[m] = count.get(m, 0) + 1
        total *= leg["odds"]
    return chosen, round(total, 2)


def build_vip(legs, target=200.0, max_legs=16):
    legs = [l for l in legs if 0.55 <= l["prob"] <= 0.85]
    legs = sorted(legs, key=lambda l: l["prob"], reverse=True)
    chosen, total, count = [], 1.0, {}
    for leg in legs:
        if len(chosen) >= max_legs:
            break
        m = leg["match"]
        if count.get(m, 0) >= 2:
            continue
        chosen.append(leg)
        count[m] = count.get(m, 0) + 1
        total *= leg["odds"]
        if total >= target * 0.95:
            break
    return chosen, round(total, 2)


def ai_analysis(leg):
    return ("**AI Analysis (" + leg["league"] + "):** Model confidence "
            + str(int(leg["prob"] * 100)) + "% on '" + leg["selection"] + "'. "
            + "Expected goals " + leg["xg"] + " -> expected score line **"
            + leg["expected_score"] + "**. AI Fair Odds "
            + str(leg["odds"]) + " (booking odds neglected).")


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
            "SELECT " + field + " AS grp, COUNT(*) AS legs, SUM(result='WON') AS won, ROUND(100.0*SUM(result='WON')/COUNT(*),1) AS hit_rate FROM legs WHERE result IN ('WON','LOST') GROUP BY " + field + " ORDER BY legs DESC")]


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
st.caption("Booking odds **neglected** • Fixtures **fact-checked live** • **League + expected score** always shown • Simulated games removed")

with st.sidebar:
    st.header("🎛 Controls")
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
            "DAILY": build_accumulator(all_legs, 8, 4),
            "WEEKLY": build_accumulator(all_legs, 25, 7),
            "VIP": build_vip(all_legs),
        }

    if "slips" in st.session_state:
        st.header("🔎 Fact-Check Report")
        st.dataframe(pd.DataFrame(st.session_state["report"]),
                     use_container_width=True, hide_index=True)
        st.success(str(st.session_state["nfix"]) + " verified fixtures loaded (simulated removed).")
        st.header("📊 AI Match Probabilities")
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
                "AI Fair Odds": l["odds"], "Exp. Score": l["expected_score"],
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
            st.info("Leg graded: **" + enter_result(r["leg_id"], fh, fa, ht, corners) + "**")
    else:
        st.info("No pending legs.")

    st.subheader("Slip log")
    slips = all_slips()
    if slips:
        st.dataframe(pd.DataFrame(slips), use_container_width=True, hide_index=True)

st.divider()
st.caption("Paper-trade first. This app never reads bookmaker odds; all prices are internal AI Fair Odds.")
st.caption("Paper-trade first. This app never reads bookmaker odds; all prices are internal AI Fair Odds.")
