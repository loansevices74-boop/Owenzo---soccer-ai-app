# ============================================================
# OWENZO FOOTBALL AI — app.py (SINGLE-FILE FINAL)
# Tabs: Predict & Slips | Tracker & ROI | Euro Hub
# No external modules needed. Markdown tables = version-proof.
# ============================================================
import os, sqlite3, time
from math import exp, factorial
from datetime import datetime, timedelta
import requests
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Owenzo Football AI", page_icon="⚽", layout="wide")

API = "https://www.thesportsdb.com/api/v1/json/3"
SEASON_START = datetime(2026, 8, 14)

EURO_LEAGUES = {
    "🏴 English Premier League": 4328,
    "🇪🇸 Spanish La Liga": 4335,
    "🇮🇹 Italian Serie A": 4332,
    "🇩🇪 German Bundesliga": 4331,
    "🇫🇷 French Ligue 1": 4334,
    "🇳 Dutch Eredivisie": 4337,
    "🇵🇹 Portuguese Primeira Liga": 4344,
    "🏴 Scottish Premiership": 4346,
    "🇸🇪 Swedish Allsvenskan": 4348,
    "🇳 Norwegian Eliteserien": 4349,
    "🇩🇰 Danish Superliga": 4350,
    "🇨🇭 Swiss Super League": 4351,
    "🇦🇹 Austrian Bundesliga": 4352,
    "🇧🇪 Belgian Pro League": 4353,
    "🇹 Turkish Süper Lig": 4354,
    "🇬 Greek Super League": 4355,
    "🇵🇱 Polish Ekstraklasa": 4362,
    "🇭🇷 Croatian HNL": 4363,
    "🇺 Ukrainian Premier League": 4364,
}

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)

@st.cache_data(ttl=6 * 3600)
def _get(url):
    for _ in range(3):
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(2)
    return {}

def fixtures_on(d):
    return _get(f"{API}/eventsday.php?d={d}&s=Soccer").get("events") or []

def league_next(lid):
    return _get(f"{API}/eventsnextleague.php?id={lid}").get("events") or []

def league_past(lid):
    return _get(f"{API}/eventspastleague.php?id={lid}").get("events") or []

def team_form(results, team):
    games = []
    for e in results:
        h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
        hs, as_ = e.get("intHomeScore"), e.get("intAwayScore")
        if team in (h, a) and hs not in (None, "") and as_ not in (None, ""):
            try:
                hs, as_ = int(hs), int(as_)
            except Exception:
                continue
            games.append((hs, as_) if team == h else (as_, hs))
    games = games[-6:]
    n = len(games)
    if n == 0:
        return 1.3, 1.3, 0
    w = [1.0, .85, .72, .61, .52, .44][:n]
    gf = sum(g * wi for (g, c), wi in zip(games, w)) / sum(w)
    ga = sum(c * wi for (g, c), wi in zip(games, w)) / sum(w)
    return (gf * n + 1.3 * 3) / (n + 3), (ga * n + 1.3 * 3) / (n + 3), n

def team_stats(results):
    out = {}
    for e in results:
        for side in ("H", "A"):
            t = e.get("strHomeTeam") if side == "H" else e.get("strAwayTeam")
            gs = e.get("intHomeScore") if side == "H" else e.get("intAwayScore")
            gc = e.get("intAwayScore") if side == "H" else e.get("intHomeScore")
            if t and gs not in (None, ""):
                g, c, n = out.get(t, (0.0, 0.0, 0))
                out[t] = (g + int(gs), c + int(gc), n + 1)
    return {t: (g / n, c / n, n) for t, (g, c, n) in out.items() if n >= 2}

def pois(k, lam):
    return exp(-lam) * lam ** k / factorial(k)

def score_probs(lh, la, maxg=6):
    ph = pd = pa = po15 = po25 = pbtts = 0.0
    for i in range(maxg + 1):
        for j in range(maxg + 1):
            p = pois(i, lh) * pois(j, la)
            if i > j: ph += p
            elif i == j: pd += p
            else: pa += p
            if i + j >= 2: po15 += p
            if i + j >= 3: po25 += p
            if i >= 1 and j >= 1: pbtts += p
    return ph, pd, pa, po15, po25, pbtts

def probs_for(res, h, a):
    hgf, hga, nh = team_form(res, h)
    agf, aga, na = team_form(res, a)
    lh = min(3.2, max(0.4, (hgf + aga) / 2 * 1.15))
    la = min(3.2, max(0.4, (agf + hga) / 2 * 0.92))
    return score_probs(lh, la), min(nh, na)

def top_pick(ph, pd, pa, po15, po25, pbtts, allow_draw=True):
    c = [("Home", ph), ("Away", pa), ("Over 1.5", po15),
         ("Over 2.5", po25), ("BTTS", pbtts)]
    if allow_draw:
        c.append(("Draw", pd))
    name, p = max(c, key=lambda x: x[1])
    return name, p

def current_mw():
    return int((datetime.now() - SEASON_START).days // 7) + 1

def matrix_picks():
    picks = []
    for name, lid in EURO_LEAGUES.items():
        stats = team_stats(league_past(lid))
        for e in league_next(lid)[:6]:
            h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
            if h not in stats or a not in stats:
                continue
            hg, hc, _ = stats[h]
            ag, ac, _ = stats[a]
            comb = hg + ag
            dd = e.get("dateEvent") or ""
            if hg >= 1.2 and ag >= 1.2 and hc >= 0.8 and ac >= 0.8 and comb >= 2.8:
                picks.append([dd, name, f"{h} vs {a}", "Over 1.5", "85-95%"])
            if hg >= 1.5 and ag >= 1.5 and hc >= 1.0 and ac >= 1.0 and comb >= 3.2:
                picks.append([dd, name, f"{h} vs {a}", "Over 2.5", "85-90%"])
            if abs(hg - ag) <= 0.3 and hc < 1.0 and ac < 1.0 and hg <= 1.5 and ag <= 1.5:
                picks.append([dd, name, f"{h} vs {a}", "Draw", "80-89%"])
    return picks

st.title("⚽ Owenzo Football AI — Live Prediction Engine")
st.caption("v8 FINAL • Poisson + recency-weighted form • auto-refresh 6h")

bankroll = st.sidebar.number_input("Bankroll ($)", value=1000, step=100)
st.sidebar.markdown("🔞 18+ • Entertainment only. Paper-traded system.")

t1, t2, t3 = st.tabs(["🎯 Predict & Slips", "📊 Tracker & ROI", "🇪🇺 Euro Hub"])

# ================= TAB 1 =================
with t1:
    d = st.date_input("Match date", value=datetime.utcnow().date() + timedelta(days=1))
    evs = fixtures_on(d.strftime("%Y-%m-%d"))
    if not evs:
        st.info("No soccer fixtures published for this date yet.")
    else:
        rows, disp = [], []
        for e in evs:
            lid = e.get("idLeague")
            h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
            if not lid or not h or not a:
                continue
            (ph, pd, pa, po15, po25, pbtts), ndata = probs_for(league_past(lid), h, a)
            pick, conf = top_pick(ph, pd, pa, po15, po25, pbtts)
            leg, legp = top_pick(ph, pd, pa, po15, po25, pbtts, allow_draw=False)
            disp.append([e.get("strLeague", ""), f"{h} vs {a}", int(ph * 100),
                         int(pd * 100), int(pa * 100), int(po15 * 100),
                         int(po25 * 100), int(pbtts * 100), pick, int(conf * 100)])
            rows.append({"_leg": leg, "_legp": legp, "_ok": ndata >= 2,
                         "Match": f"{h} vs {a}", "CONF": int(conf * 100)})
        rows.sort(key=lambda r: r["CONF"], reverse=True)

        st.markdown(f"### 📅 All fixtures — {d} ({len(rows)} analysed)")
        st.markdown(md_table(["League", "Match", "1", "X", "2", "O1.5", "O2.5",
                              "BTTS", "TOP PICK", "CONF"], disp[:25]))

        stake = round(bankroll * 0.005, 2)
        legs, prod = [], 1.0
        for r in rows:
            if not r["_ok"] or r["_legp"] < 0.55 or len(legs) >= 4:
                continue
            odds = round(1 / r["_legp"], 2)
            if prod * odds > 8.0:
                continue
            prod *= odds
            legs.append((r["Match"], r["_leg"], int(r["_legp"] * 100), odds))

        st.markdown("### 🎯 Daily Value Slip (max 4 legs, odds cap 8.0, stake 0.5%)")
        if legs:
            for m, mk, c, o in legs:
                st.markdown(f"- **{m}** → {mk} ({c}%) • model odds {o}")
            st.success(f"Combined model odds: **{round(prod, 2)}x** • Stake: "
                       f"**${stake}** • Potential return: **${round(stake * prod, 2)}**")
            lines = [f"{i}) {m} → {mk} ({c}%)" for i, (m, mk, c, o) in enumerate(legs, 1)]
            tik = ("⚽ OWENZO AI DAILY SLIP 🤖\n" + "\n".join(lines) +
                   f"\n📈 Model odds: {round(prod, 2)}x\n🔞 18+ | Entertainment only")
            st.code(tik)
        else:
            st.warning("No value legs pass the filters for this date (selectivity = quality).")

# ================= TAB 2 =================
with t2:
    df = None
    if os.path.exists("soccer_tracker.db"):
        try:
            con = sqlite3.connect("soccer_tracker.db")
            df = pd.read_sql("select * from slips order by rowid desc limit 30", con)
            con.close()
        except Exception:
            df = None
    if df is None or df.empty:
        st.info("Graded results sync from the Telegram bot after each matchday.")
    else:
        st.markdown(md_table([str(c) for c in df.columns], df.values.tolist()))
        for col in ("profit", "pnl", "return"):
            if col in df.columns:
                st.metric("Total P/L", round(pd.to_numeric(df[col], errors="coerce").sum(), 2))
                break

# ================= TAB 3: EURO HUB =================
with t3:
    st.subheader("🇪🇺 European Leagues — Daily Prediction Board")
    st.caption("Top 8 + all tracked European leagues • 🔴 TODAY on matchdays")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    shown = 0
    for name, lid in EURO_LEAGUES.items():
        fixtures = league_next(lid)
        if not fixtures:
            continue
        res = league_past(lid)
        shown += 1
        st.markdown(f"### {name}")
        rows = []
        for e in fixtures[:6]:
            h, a = e.get("strHomeTeam"), e.get("strAwayTeam")
            dd = "🔴 TODAY" if (e.get("dateEvent") or "") == today else (e.get("dateEvent") or "")
            (ph, pd, pa, po15, po25, pbtts), _ = probs_for(res, h, a)
            pick, conf = top_pick(ph, pd, pa, po15, po25, pbtts)
            rows.append([dd, f"{h} vs {a}", f"{int(ph*100)}%", f"{int(pd*100)}%",
                         f"{int(pa*100)}%", f"{int(po15*100)}%", f"{int(po25*100)}%",
                         f"{int(pbtts*100)}%", f"{pick} ({int(conf*100)}%)"])
        st.markdown(md_table(["Date", "Match", "1", "X", "2", "O1.5", "O2.5", "BTTS", "TOP PICK"], rows))
    if shown == 0:
        st.info("Waiting for fixture data from the API...")

    st.divider()
    mw = current_mw()
    st.markdown(f"### 🎯 Euro Matrix Picks (Matchweek {mw})")
    if mw < 3:
        st.info(f"Matrix activates at MW3. Currently MW{mw} — clusters building below.")
    else:
        picks = matrix_picks()
        if picks:
            st.markdown(md_table(["Date", "League", "Match", "PICK", "CONF"], picks))
        else:
            st.info("No matrix tags fired today — selectivity = quality.")

    st.markdown("### 🧠 Early-Season Clusters (live)")
    for name, lid in EURO_LEAGUES.items():
        stats = team_stats(league_past(lid))
        attack = [t for t, (gf, ga, n) in stats.items() if gf >= 1.5 or (gf >= 1.2 and ga >= 1.0)]
        fortress = [t for t, (gf, ga, n) in stats.items() if ga <= 0.8 and gf <= 1.3]
        if attack or fortress:
            st.markdown(f"**{name}** — 🔥 {', '.join(attack[:6]) or '—'} | 🧱 {', '.join(fortress[:6]) or '—'}")
