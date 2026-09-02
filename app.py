# Soccer AI Prediction Web App - v6 (Euro Edge integrated)
# Pipeline: fact-check -> deep team analysis (venue splits, shrinkage)
#           -> Dixon-Coles score model -> expected score -> mixed slips
#           + Euro Early-Season Statistical Tracking & Predictive Engine
import os
import sqlite3
import math
import requests
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_BASE = "https://www.thesportsdb.com/api/v1/json/3"
DB_NAME = os.environ.get("OWENZO_DB", "soccer_tracker.db")
SLIPS_TABLE = "slips"

# Slip-building rules (kept from v8)
MAX_LEGS = 4
MAX_ODDS = 8.0
STAKE_PCT = 0.005          # 0.5% of bankroll per slip
DEFAULT_BANKROLL = 1000.0

# Confidence cap — honest ceiling for a model estimate (never 100%)
CONFIDENCE_CAP = 0.78

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def md_table(df: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub-style markdown table (kept from v8)."""
    if df is None or df.empty:
        return "_No data._"
    cols = [str(c) for c in df.columns]
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if pd.isna(v):
                cells.append("")
            else:
                cells.append(str(v).replace("|", "\\|"))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + rows)


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _api_get(path, params=None, timeout=12):
    """Thin wrapper around TheSportsDB GET with graceful failure."""
    try:
        r = requests.get(f"{API_BASE}/{path}", params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"API error ({path}): {exc}")
        return {}


# ---------------------------------------------------------------------------
# Database access (slips table)
# ---------------------------------------------------------------------------
def _connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _slip_columns(conn):
    cur = conn.execute(f"PRAGMA table_info({SLIPS_TABLE})")
    return [row["name"] for row in cur.fetchall()]


def load_slips():
    """Load graded slips. Returns (df, has_date_col, date_col_name)."""
    if not os.path.exists(DB_NAME):
        return pd.DataFrame(), False, None
    try:
        conn = _connect()
        cols = _slip_columns(conn)
        if not cols:
            conn.close()
            return pd.DataFrame(), False, None
        df = pd.read_sql_query(f"SELECT * FROM {SLIPS_TABLE}", conn)
        conn.close()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not read slips table: {exc}")
        return pd.DataFrame(), False, None

    if df.empty:
        return df, False, None

    # Normalise the profit column (accept profit / pnl / return / pl)
    profit_col = None
    for cand in ("profit", "pnl", "return", "pl", "p_l", "net"):
        if cand in df.columns:
            profit_col = cand
            break
    if profit_col is not None:
        df["_profit"] = pd.to_numeric(df[profit_col], errors="coerce").fillna(0.0)
    elif "stake" in df.columns and "return" in df.columns:
        df["_profit"] = (pd.to_numeric(df["return"], errors="coerce").fillna(0.0)
                         - pd.to_numeric(df["stake"], errors="coerce").fillna(0.0))
    else:
        df["_profit"] = 0.0

    # Find a usable date column
    date_col = None
    for cand in ("date", "slip_date", "created_at", "timestamp", "graded_at", "settled_at"):
        if cand in df.columns:
            date_col = cand
            break

    if date_col is not None:
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
        has_date = bool(df["_date"].notna().any())
    else:
        has_date = False

    return df, has_date, date_col


# ---------------------------------------------------------------------------
# P&L aggregation (Daily / Weekly / Monthly)
# ---------------------------------------------------------------------------
def compute_pnl_summary(df, has_date):
    """Return a dict with daily/weekly/monthly totals + a trend table."""
    empty = {
        "daily": 0.0, "weekly": 0.0, "monthly": 0.0,
        "total": 0.0, "wins": 0, "losses": 0, "slips": 0,
        "trend": pd.DataFrame(), "has_date": has_date,
    }
    if df is None or df.empty:
        return empty

    total = float(df["_profit"].sum())
    wins = int((df["_profit"] > 0).sum())
    losses = int((df["_profit"] < 0).sum())
    slips = int(len(df))

    if not has_date:
        empty.update(total=total, wins=wins, losses=losses, slips=slips)
        return empty

    today = pd.Timestamp.now().normalize()
    d = df[df["_date"].notna()].copy()
    d["_day"] = d["_date"].dt.normalize()
    d["_week"] = d["_date"].dt.to_period("W").apply(lambda x: x.start_time)
    d["_month"] = d["_date"].dt.to_period("M").apply(lambda x: x.start_time)

    daily = float(d[d["_day"] == today]["_profit"].sum())
    week_start = today - pd.Timedelta(days=today.weekday())
    weekly = float(d[d["_week"] >= week_start]["_profit"].sum())
    month_start = today.replace(day=1)
    monthly = float(d[d["_month"] >= month_start]["_profit"].sum())

    # Small aggregation / trend table (by day, last 14 days)
    trend = (d.groupby("_day")["_profit"]
              .sum().reset_index().sort_values("_day", ascending=False)
              .head(14))
    trend.columns = ["Date", "P/L"]
    trend["Date"] = trend["Date"].dt.strftime("%Y-%m-%d")
    trend["P/L"] = trend["P/L"].round(2)

    return {
        "daily": daily, "weekly": weekly, "monthly": monthly,
        "total": total, "wins": wins, "losses": losses, "slips": slips,
        "trend": trend, "has_date": has_date,
    }


# ---------------------------------------------------------------------------
# Model signals (deeper analysis)
# ---------------------------------------------------------------------------
def _poisson_pmf(k, lam):
    """Poisson probability mass function."""
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_joint(mu_home, mu_away, max_goals=8):
    """Joint score probability under independent Poisson goals."""
    probs = {}
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            probs[(i, j)] = _poisson_pmf(i, mu_home) * _poisson_pmf(j, mu_away)
    return probs


def _expected_goals(team_attack, opp_defence, league_avg):
    """Basic expected goals from attack/defence ratings vs league average."""
    return max(0.05, league_avg * team_attack * opp_defence)


def _form_streak(team):
    """+1..-1 based on last 5 results (W=+1, D=0, L=-1)."""
    form = team.get("form", [])
    if not form:
        return 0.0
    recent = form[-5:]
    score = sum({"W": 1.0, "D": 0.0, "L": -1.0}.get(r, 0.0) for r in recent)
    return score / max(1, len(recent))


def _home_away_split(team, venue):
    """Win-rate (draws count half) at home or away, 0..1."""
    rec = team.get(f"{venue}_record", {})
    w = _safe_float(rec.get("wins", 0))
    d = _safe_float(rec.get("draws", 0))
    l = _safe_float(rec.get("losses", 0))
    tot = w + d + l
    if tot == 0:
        return 0.0
    return (w + 0.5 * d) / tot


def _defensive_strength(team):
    """Higher = better defence (fewer goals conceded per game), 0..1."""
    conceded = _safe_float(team.get("goals_conceded", 0))
    played = max(1, _safe_float(team.get("games_played", 1), 1))
    gpg = conceded / played
    return max(0.0, min(1.0, 1.0 - gpg / 3.0))


def _league_gpg(team, fallback=1.35):
    """League goals-per-game average for a team (fallback if unknown)."""
    return _safe_float(team.get("league_gpg", fallback), fallback)


def compute_match_signals(home, away, league_avg=1.35):
    """
    Combine several signals into a per-outcome confidence estimate.
    Returns dict with probabilities, signals breakdown, and confidence.
    """
    # 1) Poisson score probabilities from recency-weighted form
    mu_home = _expected_goals(
        _safe_float(home.get("attack", 1.0), 1.0),
        _safe_float(away.get("defence", 1.0), 1.0),
        league_avg,
    )
    mu_away = _expected_goals(
        _safe_float(away.get("attack", 1.0), 1.0),
        _safe_float(home.get("defence", 1.0), 1.0),
        league_avg,
    )
    probs = _poisson_joint(mu_home, mu_away)

    p_home = sum(p for (h, a), p in probs.items() if h > a)
    p_draw = sum(p for (h, a), p in probs.items() if h == a)
    p_away = sum(p for (h, a), p in probs.items() if h < a)

    # 2) Recent form streaks (last 5)
    home_streak = _form_streak(home)
    away_streak = _form_streak(away)

    # 3) Home/away splits
    home_home = _home_away_split(home, "home")
    away_away = _home_away_split(away, "away")

    # 4) Defensive strength (goals conceded per game, inverted)
    home_def = _defensive_strength(home)
    away_def = _defensive_strength(away)

    # 5) League goals-per-game averages
    league_gpg = _league_gpg(home, league_avg)

    # Combine into a raw confidence for the most likely outcome
    best_outcome = max([("Home", p_home), ("Draw", p_draw), ("Away", p_away)],
                       key=lambda x: x[1])
    raw_conf = best_outcome[1]

    # Adjust with form / split / defence signals (small, bounded deltas)
    if best_outcome[0] == "Home":
        raw_conf += 0.02 * home_streak - 0.02 * away_streak
        raw_conf += 0.02 * home_home - 0.02 * away_away
        raw_conf += 0.02 * home_def - 0.02 * away_def
    elif best_outcome[0] == "Away":
        raw_conf += 0.02 * away_streak - 0.02 * home_streak
        raw_conf += 0.02 * away_away - 0.02 * home_home
        raw_conf += 0.02 * away_def - 0.02 * home_def
    else:
        raw_conf += 0.01 * (home_streak - away_streak)

    # Cap confidence at a realistic ceiling (model estimate, not guarantee)
    confidence = min(CONFIDENCE_CAP, max(0.0, raw_conf))

    signals = {
        "poisson_home": round(p_home, 3),
        "poisson_draw": round(p_draw, 3),
        "poisson_away": round(p_away, 3),
        "home_streak": round(home_streak, 2),
        "away_streak": round(away_streak, 2),
        "home_home_split": round(home_home, 2),
        "away_away_split": round(away_away, 2),
        "home_defence": round(home_def, 2),
        "away_defence": round(away_def, 2),
        "league_gpg": round(league_gpg, 2),
        "mu_home": round(mu_home, 2),
        "mu_away": round(mu_away, 2),
    }
    return {
        "outcome": best_outcome[0],
        "confidence": round(confidence, 3),
        "signals": signals,
        "probs": probs,
    }


# ---------------------------------------------------------------------------
# Fact-check layer
# ---------------------------------------------------------------------------
def fact_check(pick, h2h, home_form, away_form):
    """
    Cross-validate a pick against recent head-to-head and current form.
    Returns (passed: bool, reasons: list[str]).
    """
    reasons = []
    passed = True

    # Head-to-head check
    if h2h:
        h_wins = h2h.get("home_wins", 0)
        a_wins = h2h.get("away_wins", 0)
        draws = h2h.get("draws", 0)
        total = h_wins + a_wins + draws
        if total > 0:
            if pick == "Home" and a_wins > h_wins:
                reasons.append(f"H2H favours away ({a_wins}-{h_wins})")
                passed = False
            elif pick == "Away" and h_wins > a_wins:
                reasons.append(f"H2H favours home ({h_wins}-{a_wins})")
                passed = False
            else:
                reasons.append(f"H2H balanced ({h_wins}-{draws}-{a_wins})")

    # Current form check
    if pick == "Home" and home_form < 0.0:
        reasons.append("Home team in poor recent form")
        passed = False
    elif pick == "Away" and away_form < 0.0:
        reasons.append("Away team in poor recent form")
        passed = False
    else:
        reasons.append("Recent form supports the pick")

    return passed, reasons


# ---------------------------------------------------------------------------
# TheSportsDB data enrichment (best-effort)
# ---------------------------------------------------------------------------
def _team_signal(team_name):
    """Fetch team form / stats from TheSportsDB; return a signal dict."""
    sig = {
        "form": [], "attack": 1.0, "defence": 1.0,
        "home_record": {}, "away_record": {},
        "goals_conceded": 0, "games_played": 0, "league_gpg": 1.35,
    }
    if not team_name:
        return sig
    data = _api_get("searchteams.php", {"t": team_name})
    teams = (data or {}).get("teams") or []
    if not teams:
        return sig
    t = teams[0]

    # Recent form string like "WWDLW"
    form_str = t.get("strForm") or ""
    sig["form"] = [c for c in form_str.upper() if c in "WDL"]

    # Attack / defence ratings: TheSportsDB exposes a 0..10 strength rating
    # (strStrength / strStrengthAttack / strStrengthDefence). Normalise to ~1.0.
    atk = _safe_float(t.get("strStrengthAttack") or t.get("strStrength"), 5.0)
    dfn = _safe_float(t.get("strStrengthDefence") or t.get("strStrength"), 5.0)
    sig["attack"] = max(0.2, atk / 5.0)
    sig["defence"] = max(0.2, dfn / 5.0)

    # Home / away records (if the API provides them)
    for venue, key in (("home", "strHomeRecord"), ("away", "strAwayRecord")):
        rec = t.get(key)
        if rec:
            parts = str(rec).replace(" ", "").split("-")
            if len(parts) == 3:
                sig[f"{venue}_record"] = {
                    "wins": _safe_float(parts[0]),
                    "draws": _safe_float(parts[1]),
                    "losses": _safe_float(parts[2]),
                }
    return sig


def _h2h(home, away):
    """Fetch recent head-to-head results between two teams (best-effort)."""
    if not home or not away:
        return None
    data = _api_get("eventslast.php", {"id": home})  # last events for home team
    events = (data or {}).get("results") or []
    h2h = {"home_wins": 0, "away_wins": 0, "draws": 0}
    for ev in events:
        h = ev.get("strHomeTeam", "")
        a = ev.get("strAwayTeam", "")
        if {h, a} != {home, away}:
            continue
        hs = _safe_float(ev.get("intHomeScore"), -1)
        as_ = _safe_float(ev.get("intAwayScore"), -1)
        if hs < 0 or as_ < 0:
            continue
        if hs > as_:
            h2h["home_wins"] += 1
        elif as_ > hs:
            h2h["away_wins"] += 1
        else:
            h2h["draws"] += 1
    if sum(h2h.values()) == 0:
        return None
    return h2h


def _estimate_odds(probs, outcome):
    """Estimate decimal odds from model probability (with a margin)."""
    if outcome == "Home":
        p = sum(p for (h, a), p in probs.items() if h > a)
    elif outcome == "Away":
        p = sum(p for (h, a), p in probs.items() if h < a)
    else:
        p = sum(p for (h, a), p in probs.items() if h == a)
    p = max(0.05, min(0.95, p))
    return round(1.0 / p, 2)


# ---------------------------------------------------------------------------
# Slip building (kept from v8, extended with confidence + fact-check)
# ---------------------------------------------------------------------------
def build_slip(picks, bankroll=DEFAULT_BANKROLL):
    """
    Build a value slip from validated picks.
    Rules (kept): max 4 legs, combined odds cap 8.0, stake 0.5% of bankroll.
    """
    legs = []
    for p in picks:
        if p.get("fact_checked") is False:
            continue
        legs.append(p)
        if len(legs) >= MAX_LEGS:
            break

    if not legs:
        return None

    combined_odds = 1.0
    for p in legs:
        combined_odds *= _safe_float(p.get("odds", 1.0), 1.0)
        if combined_odds > MAX_ODDS:
            legs = legs[:-1]
            combined_odds = 1.0
            for p in legs:
                combined_odds *= _safe_float(p.get("odds", 1.0), 1.0)
            break

    stake = round(bankroll * STAKE_PCT, 2)
    return {
        "legs": legs,
        "combined_odds": round(combined_odds, 2),
        "stake": stake,
        "potential_return": round(stake * combined_odds, 2),
        "avg_confidence": round(
            sum(_safe_float(p.get("confidence", 0)) for p in legs) / len(legs), 3
        ),
    }


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Owenzo Football AI", layout="wide")

st.title("⚽ Owenzo Football AI")
st.caption(
    "Poisson + recency-weighted form model with a fact-check layer. "
    "**Predictions are probabilistic model estimates — NOT guarantees.** "
    "No model can achieve 100% daily accuracy. Bet responsibly."
)

tab_predict, tab_tracker, tab_euro = st.tabs(
    ["Predict & Slips", "Tracker & ROI", "Euro Hub"]
)

# ---------------------------------------------------------------------------
# Tab 1 — Predict & Slips
# ---------------------------------------------------------------------------
with tab_predict:
    st.subheader("🔮 Predict & Value Slips")

    league = st.selectbox(
        "League",
        ["English Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1"],
    )
    league_id = {"English Premier League": "4328", "La Liga": "4335",
                 "Serie A": "4332", "Bundesliga": "4331", "Ligue 1": "4334"}[league]

    if st.button("Generate Predictions", type="primary"):
        with st.spinner("Fetching fixtures & building model signals..."):
            data = _api_get("eventsnextleague.php", {"id": league_id})
            fixtures = (data or {}).get("events", []) or []

        if not fixtures:
            st.info("No upcoming fixtures found for this league right now.")
        else:
            rows = []
            for fx in fixtures[:12]:
                home = fx.get("strHomeTeam", "?")
                away = fx.get("strAwayTeam", "?")
                home_data = _team_signal(home)
                away_data = _team_signal(away)
                h2h = _h2h(home, away)

                sig = compute_match_signals(home_data, away_data)
                passed, reasons = fact_check(
                    sig["outcome"], h2h,
                    _form_streak(home_data), _form_streak(away_data),
                )
                odds = _estimate_odds(sig["probs"], sig["outcome"])

                rows.append({
                    "Fixture": f"{home} vs {away}",
                    "Pick": sig["outcome"],
                    "Confidence": f"{sig['confidence']*100:.0f}%",
                    "Odds": f"{odds:.2f}",)}
