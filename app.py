"""
Owenzo Football AI (public) + Owenzõ Soccer AI Vip-01 (VIP tabs 4-7)
FINAL: multi-market picker (1X2 / O1.5 / O2.5 / BTTS / DC) + multi-league 2-odds
       + auto daily board + hidden API key + auto-record slip
"""
import os, math, hashlib, secrets, sqlite3
from datetime import datetime
import requests
import pandas as pd
import streamlit as st

API_BASE = "https://www.thesportsdb.com/api/v1/json/3"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
DB_NAME = os.environ.get("OWENZO_DB", "soccer_tracker.db")
AUTH_DB = os.environ.get("OWENZO_AUTH_DB", "owenzo_auth.db")
SLIPS_TABLE = "slips"
MAX_LEGS = 4
MAX_ODDS = 8.0
STAKE_PCT = 0.005
DEFAULT_BANKROLL = 1000.0
CONFIDENCE_CAP = 0.78
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "owenzo2026"
ODDS_SPORT_KEYS = {"English Premier League": "soccer_epl", "La Liga": "soccer_spain_la_liga",
                   "Serie A": "soccer_italy_serie_a", "Bundesliga": "soccer_germany_bundesliga",
                   "Ligue 1": "soccer_france_ligue_one"}
LEAGUE_IDS = {"English Premier League": "4328", "La Liga": "4335", "Serie A": "4332",
              "Bundesliga": "4331", "Ligue 1": "4334"}
LEAGUES = list(LEAGUE_IDS.keys())

def _as_list(v):
    return v if isinstance(v, list) else []

def md_table(df):
    if df is None or df.empty:
        return "_No data._"
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if pd.isna(r[c]) else str(r[c]).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(out)

def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default

def _api_get(base, path, params=None, timeout=12):
    try:
        r = requests.get(f"{base}/{path}", params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.warning(f"API error ({path}): {exc}")
        return {}

# ---------------- auth ----------------
def _auth_connect():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pass_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT)")
    conn.commit()
    return conn

def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"

def _verify_password(password, stored):
    try:
        salt, digest = stored.split("$", 1)
        return secrets.compare_digest(_hash_password(password, salt).split("$", 1)[1], digest)
    except Exception:
        return False

def _ensure_default_admin():
    conn = _auth_connect()
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (DEFAULT_ADMIN_USER,)).fetchone() is None:
        conn.execute("INSERT INTO users VALUES (?,?,?,?)",
                     (DEFAULT_ADMIN_USER, _hash_password(DEFAULT_ADMIN_PASS), "admin", datetime.now().isoformat()))
        conn.commit()
    conn.close()

def authenticate(username, password):
    conn = _auth_connect()
    row = conn.execute("SELECT pass_hash FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return bool(row and _verify_password(password, row["pass_hash"]))

def is_admin(username):
    conn = _auth_connect()
    row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return bool(row and row["role"] == "admin")

def list_users():
    conn = _auth_connect()
    rows = conn.execute("SELECT username, role, created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_user(username, password, role="user"):
    conn = _auth_connect()
    try:
        conn.execute("INSERT INTO users VALUES (?,?,?,?)",
                     (username, _hash_password(password), role, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def revoke_user(username):
    if username == DEFAULT_ADMIN_USER:
        return False
    conn = _auth_connect()
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return True

def generate_strong_password(length=14):
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def change_password(username, current_password, new_password):
    conn = _auth_connect()
    row = conn.execute("SELECT pass_hash FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not _verify_password(current_password, row["pass_hash"]):
        conn.close()
        return False, "Current password is incorrect."
    conn.execute("UPDATE users SET pass_hash = ? WHERE username = ?", (_hash_password(new_password), username))
    conn.commit()
    conn.close()
    return True, "Password updated successfully."

def admin_set_password(username, new_password):
    conn = _auth_connect()
    conn.execute("UPDATE users SET pass_hash = ? WHERE username = ?", (_hash_password(new_password), username))
    conn.commit()
    conn.close()

# ---------------- settings & bets ----------------
SETTINGS_DEFAULTS = {"stake_pct": STAKE_PCT, "edge_threshold": 0.05, "bankroll": DEFAULT_BANKROLL,
                     "max_legs": MAX_LEGS, "max_odds": MAX_ODDS}

def _settings_connect():
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn

def load_settings():
    s = dict(SETTINGS_DEFAULTS)
    try:
        conn = _settings_connect()
        for row in conn.execute("SELECT key, value FROM settings"):
            s[row["key"]] = row["value"]
        conn.close()
    except Exception:
        pass
    return s

def save_setting(key, value):
    try:
        conn = _settings_connect()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception:
        pass

BETS_TABLE = "bets"

def _bets_connect():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE IF NOT EXISTS {BETS_TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, timestamp TEXT, fixture TEXT, pick TEXT, odds REAL, stake REAL, result TEXT DEFAULT 'pending', pnl REAL DEFAULT 0.0, running_balance REAL)")
    conn.commit()
    return conn

def record_bet(username, fixture, pick, odds, stake, result="pending"):
    conn = _bets_connect()
    cur = conn.execute(f"INSERT INTO {BETS_TABLE} (username, timestamp, fixture, pick, odds, stake, result, pnl, running_balance) VALUES (?,?,?,?,?,?,?,?,?)",
                       (username, datetime.now().isoformat(), fixture, pick, odds, stake, result, 0.0, 0.0))
    conn.commit()
    bet_id = cur.lastrowid
    conn.close()
    _recompute_running_balance()
    return bet_id

def _recompute_running_balance():
    conn = _bets_connect()
    rows = conn.execute(f"SELECT id, pnl FROM {BETS_TABLE} ORDER BY timestamp ASC, id ASC").fetchall()
    bal = float(load_settings().get("bankroll", DEFAULT_BANKROLL))
    for r in rows:
        bal += _safe_float(r["pnl"])
        conn.execute(f"UPDATE {BETS_TABLE} SET running_balance = ? WHERE id = ?", (round(bal, 2), r["id"]))
    conn.commit()
    conn.close()

def load_bets():
    conn = _bets_connect()
    rows = conn.execute(f"SELECT * FROM {BETS_TABLE} ORDER BY timestamp ASC, id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def bankroll_stats():
    bets = load_bets()
    start = float(load_settings().get("bankroll", DEFAULT_BANKROLL))
    if not bets:
        return {"current": start, "total_pnl": 0.0, "wins": 0, "losses": 0,
                "pending": 0, "win_rate": 0.0, "bets": 0, "start": start}
    total_pnl = sum(_safe_float(b["pnl"]) for b in bets)
    wins = sum(1 for b in bets if b["result"] == "win")
    losses = sum(1 for b in bets if b["result"] == "loss")
    pending = sum(1 for b in bets if b["result"] == "pending")
    settled = wins + losses
    return {"current": round(start + total_pnl, 2), "total_pnl": round(total_pnl, 2),
            "wins": wins, "losses": losses, "pending": pending,
            "win_rate": round(wins / settled, 4) if settled else 0.0, "bets": len(bets), "start": start}

# ---------------- slips DB + AUTO-RECORD ----------------
def load_slips():
    if not os.path.exists(DB_NAME):
        return pd.DataFrame(), False, None
    try:
        conn = sqlite3.connect(DB_NAME)
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({SLIPS_TABLE})")]
        if not cols:
            conn.close()
            return pd.DataFrame(), False, None
        df = pd.read_sql_query(f"SELECT * FROM {SLIPS_TABLE}", conn)
        conn.close()
    except Exception as exc:
        st.warning(f"Could not read slips table: {exc}")
        return pd.DataFrame(), False, None
    if df.empty:
        return df, False, None
    profit_col = next((c for c in ("profit", "pnl", "return", "pl", "p_l", "net") if c in df.columns), None)
    if profit_col is not None:
        df["_profit"] = pd.to_numeric(df[profit_col], errors="coerce").fillna(0.0)
    elif "stake" in df.columns and "return" in df.columns:
        df["_profit"] = pd.to_numeric(df["return"], errors="coerce").fillna(0.0) - pd.to_numeric(df["stake"], errors="coerce").fillna(0.0)
    else:
        df["_profit"] = 0.0
    date_col = next((c for c in ("date", "slip_date", "created_at", "timestamp", "graded_at", "settled_at") if c in df.columns), None)
    if date_col is not None:
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
        has_date = bool(df["_date"].notna().any())
    else:
        has_date = False
    return df, has_date, date_col

def auto_record_slip(slip):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_NAME)
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({SLIPS_TABLE})")]
        if not cols:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {SLIPS_TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, slip_text TEXT, legs INTEGER, combined_odds REAL, stake REAL, result TEXT DEFAULT 'pending', profit REAL DEFAULT 0.0)")
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({SLIPS_TABLE})")]
        if "date" in cols:
            if conn.execute(f"SELECT COUNT(*) FROM {SLIPS_TABLE} WHERE date = ?", (today,)).fetchone()[0]:
                conn.close()
                return False
        leg_text = " | ".join(f"{p['fixture']} → {p['outcome']}" for p in slip["legs"])
        now = datetime.now().isoformat()
        vals = {"date": today, "slip_text": leg_text, "legs": len(slip["legs"]),
                "combined_odds": slip["combined_odds"], "stake": slip["stake"],
                "result": "pending", "profit": 0.0, "timestamp": now,
                "created_at": now, "slip_date": today}
        use = [c for c in cols if c in vals]
        if not use:
            conn.close()
            return False
        conn.execute(f"INSERT INTO {SLIPS_TABLE} ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
                     [vals[c] for c in use])
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def compute_pnl_summary(df, has_date):
    empty = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0, "total": 0.0, "wins": 0,
             "losses": 0, "slips": 0, "trend": pd.DataFrame(), "has_date": has_date}
    if df is None or df.empty:
        return empty
    total = float(df["_profit"].sum())
    wins = int((df["_profit"] > 0).sum()); losses = int((df["_profit"] < 0).sum()); slips = int(len(df))
    if not has_date:
        empty.update(total=total, wins=wins, losses=losses, slips=slips)
        return empty
    today = pd.Timestamp.now().normalize()
    d = df[df["_date"].notna()].copy()
    d["_day"] = d["_date"].dt.normalize()
    d["_week"] = d["_date"].dt.to_period("W").apply(lambda x: x.start_time)
    d["_month"] = d["_date"].dt.to_period("M").apply(lambda x: x.start_time)
    trend = d.groupby("_day")["_profit"].sum().reset_index().sort_values("_day", ascending=False).head(14)
    trend.columns = ["Date", "P/L"]
    trend["Date"] = trend["Date"].dt.strftime("%Y-%m-%d")
    trend["P/L"] = trend["P/L"].round(2)
    return {"daily": float(d[d["_day"] == today]["_profit"].sum()),
            "weekly": float(d[d["_week"] >= today - pd.Timedelta(days=today.weekday())]["_profit"].sum()),
            "monthly": float(d[d["_month"] >= today.replace(day=1)]["_profit"].sum()),
            "total": total, "wins": wins, "losses": losses, "slips": slips,
            "trend": trend, "has_date": has_date}

# ---------------- model (extended: all 8 markets) ----------------
def _poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def _poisson_matrix(mu_home, mu_away, max_goals=8):
    return {(i, j): _poisson_pmf(i, mu_home) * _poisson_pmf(j, mu_away)
            for i in range(max_goals + 1) for j in range(max_goals + 1)}

def _form_streak(form):
    if not form:
        return 0.0
    recent = form[-5:]
    return sum({"W": 1.0, "D": 0.0, "L": -1.0}.get(r, 0.0) for r in recent) / max(1, len(recent))

def _home_away_split(record, venue):
    rec = record.get(venue, {})
    w, d, l = _safe_float(rec.get("wins", 0)), _safe_float(rec.get("draws", 0)), _safe_float(rec.get("losses", 0))
    return (w + 0.5 * d) / (w + d + l) if (w + d + l) else 0.0

def _defensive_strength(conceded, played):
    return max(0.0, min(1.0, 1.0 - (conceded / max(1, played)) / 3.0))

def compute_match_signals(home, away, league_avg=1.35):
    mu_home = max(0.05, league_avg * _safe_float(home.get("attack", 1.0), 1.0) * _safe_float(away.get("defence", 1.0), 1.0)) * 1.12
    mu_away = max(0.05, league_avg * _safe_float(away.get("attack", 1.0), 1.0) * _safe_float(home.get("defence", 1.0), 1.0)) * 0.94
    mat = _poisson_matrix(mu_home, mu_away)
    p_home = sum(p for (h, a), p in mat.items() if h > a)
    p_draw = sum(p for (h, a), p in mat.items() if h == a)
    p_away = sum(p for (h, a), p in mat.items() if h < a)
    p_o15 = sum(p for (h, a), p in mat.items() if h + a >= 2)
    p_o25 = sum(p for (h, a), p in mat.items() if h + a >= 3)
    p_btts = sum(p for (h, a), p in mat.items() if h >= 1 and a >= 1)
    p_1x = p_home + p_draw
    p_x2 = p_draw + p_away
    p_12 = p_home + p_away
    hs_, as_ = _form_streak(home.get("form", [])), _form_streak(away.get("form", []))
    hh, aa = _home_away_split(home.get("record", {}), "home"), _home_away_split(away.get("record", {}), "away")
    hd = _defensive_strength(_safe_float(home.get("goals_conceded", 0)), _safe_float(home.get("games_played", 1), 1))
    ad = _defensive_strength(_safe_float(away.get("goals_conceded", 0)), _safe_float(away.get("games_played", 1), 1))
    return {
        "mu_home": mu_home, "mu_away": mu_away,
        "probs": mat,
        "m_home": p_home, "m_draw": p_draw, "m_away": p_away,
        "m_o15": p_o15, "m_o25": p_o25, "m_btts": p_btts,
        "m_1x": p_1x, "m_x2": p_x2, "m_12": p_12,
        "home_form": hs_, "away_form": as_,
        "home_home": hh, "away_away": aa,
        "home_def": hd, "away_def": ad,
        "home_attack": _safe_float(home.get("attack", 1.0), 1.0) * 1.35,
        "home_defence_raw": _safe_float(home.get("goals_conceded", 0)) / max(1, _safe_float(home.get("games_played", 1), 1)),
        "away_attack": _safe_float(away.get("attack", 1.0), 1.0) * 1.35,
        "away_defence_raw": _safe_float(away.get("goals_conceded", 0)) / max(1, _safe_float(away.get("games_played", 1), 1)),
    }

def _odds_from_prob(p):
    return round(1.0 / max(0.05, min(0.95, p)), 2)

def best_market_pick(home_sig, away_sig, sig):
    """
    Pick the single market with highest model probability across:
    Home / Draw / Away / Over 1.5 / Over 2.5 / BTTS / 1X / X2 / 12.
    Returns (market_name, raw_prob, cap_prob, odds, fact_check_passed, reason).
    """
    h_att = sig["home_attack"]
    a_att = sig["away_attack"]
    h_ga = sig["home_defence_raw"]
    a_ga = sig["away_defence_raw"]
    hf, af = sig["home_form"], sig["away_form"]
    hh, aa = sig["home_home"], sig["away_away"]

    candidates = [
        ("Home",    sig["m_home"], hf >= 0.0 or hh >= 0.4, "Home form supports pick"),
        ("Draw",    sig["m_draw"], abs(hf - af) <= 0.4,     "Even form suggests tight game"),
        ("Away",    sig["m_away"], af >= 0.0 or aa >= 0.4, "Away form supports pick"),
        ("Over 1.5", sig["m_o15"], (h_att + a_att) >= 2.0,  f"Combined attack {h_att+a_att:.2f} supports ≥2 goals"),
        ("Over 2.5", sig["m_o25"], (h_att + a_att) >= 2.6,  f"Combined attack {h_att+a_att:.2f} supports ≥3 goals"),
        ("BTTS",     sig["m_btts"], h_att >= 0.9 and a_att >= 0.9 and h_ga >= 0.7 and a_ga >= 0.7,
                                                            "Both teams score & concede consistently"),
        ("1X",       sig["m_1x"],  hf >= -0.2 or hh >= 0.35, "Home unlikely to lose"),
        ("X2",       sig["m_x2"],  af >= -0.2 or aa >= 0.35, "Away unlikely to lose"),
        ("12",       sig["m_12"],  (hf >= 0.0 or hh >= 0.4) or (af >= 0.0 or aa >= 0.4),
                                                            "Either side can win — no draw expected"),
    ]
    best = max(candidates, key=lambda x: x[1])
    raw = best[1]
    cap = min(CONFIDENCE_CAP, raw)
    return {
        "market": best[0],
        "raw_prob": raw,
        "confidence": round(cap, 3),
        "odds": _odds_from_prob(cap),
        "fact_checked": bool(best[2]),
        "reason": best[3],
    }

# ---------------- data layer ----------------
@st.cache_data(ttl=24 * 3600)
def _team_id(name):
    data = _api_get(API_BASE, "searchteams.php", {"t": name})
    teams = _as_list((data or {}).get("teams"))
    for t in teams:
        if isinstance(t, dict) and (t.get("strTeam") or "").lower() == str(name).lower():
            return str(t.get("idTeam"))
    if teams and isinstance(teams[0], dict) and teams[0].get("idTeam"):
        return str(teams[0].get("idTeam"))
    return None

def _team_result(ev):
    if not isinstance(ev, dict):
        return None
    home = ev.get("strHomeTeam") or ""
    away = ev.get("strAwayTeam") or ""
    hs = _safe_float(ev.get("intHomeScore"), -1); as_ = _safe_float(ev.get("intAwayScore"), -1)
    if not home or not away or hs < 0 or as_ < 0:
        return None
    return {"home": home, "away": away, "hs": hs, "as": as_}

@st.cache_data(ttl=6 * 3600)
def _fetch_recent_results(team_name):
    results = []
    tid = _team_id(team_name)
    attempts = [{"id": tid}] if tid else []
    attempts += [{"id": team_name}, {"t": team_name}]
    for params in attempts:
        for ev in _as_list((_api_get(API_BASE, "eventslast.php", params) or {}).get("results")):
            parsed = _team_result(ev)
            if parsed:
                results.append(parsed)
        if results:
            break
    return results[:10]

def _build_team_signal(team_name, results):
    sig = {"form": [], "attack": 1.0, "defence": 1.0,
           "record": {"home": {"wins": 0, "draws": 0, "losses": 0}, "away": {"wins": 0, "draws": 0, "losses": 0}},
           "goals_scored": 0, "goals_conceded": 0, "games_played": 0}
    if not results:
        return sig
    gf = ga = played = 0.0
    for r in results:
        if r["home"] == team_name:
            gf += r["hs"]; ga += r["as"]
            key = "home"; won, lost = r["hs"] > r["as"], r["hs"] < r["as"]
        elif r["away"] == team_name:
            gf += r["as"]; ga += r["hs"]
            key = "away"; won, lost = r["as"] > r["hs"], r["as"] < r["hs"]
        else:
            continue
        played += 1
        if won:
            sig["form"].append("W"); sig["record"][key]["wins"] += 1
        elif lost:
            sig["form"].append("L"); sig["record"][key]["losses"] += 1
        else:
            sig["form"].append("D"); sig["record"][key]["draws"] += 1
    sig["games_played"] = played; sig["goals_scored"] = gf; sig["goals_conceded"] = ga
    if played > 0:
        sig["attack"] = max(0.2, (gf / played) / 1.35)
        sig["defence"] = max(0.2, (ga / played) / 1.35)
    return sig

def _h2h(home, away, pool=None):
    h2h = {"home_wins": 0, "away_wins": 0, "draws": 0, "meetings": 0}
    for r in (pool or []):
        if {r["home"], r["away"]} != {home, away}:
            continue
        h2h["meetings"] += 1
        if r["hs"] > r["as"]: h2h["home_wins"] += 1
        elif r["as"] > r["hs"]: h2h["away_wins"] += 1
        else: h2h["draws"] += 1
    return h2h if h2h["meetings"] else None

def fact_check_1x2(pick, h2h, home_form, away_form):
    reasons = []; passed = True
    if h2h:
        hw, aw, dr = h2h.get("home_wins", 0), h2h.get("away_wins", 0), h2h.get("draws", 0)
        if pick == "Home" and aw > hw:
            reasons.append(f"H2H favours away ({aw}-{hw})"); passed = False
        elif pick == "Away" and hw > aw:
            reasons.append(f"H2H favours home ({hw}-{aw})"); passed = False
        else:
            reasons.append(f"H2H balanced ({hw}-{dr}-{aw})")
    if pick == "Home" and home_form < 0.0:
        reasons.append("Home team in poor recent form"); passed = False
    elif pick == "Away" and away_form < 0.0:
        reasons.append("Away team in poor recent form"); passed = False
    else:
        reasons.append("Recent form supports the pick")
    return passed, reasons

def _analyze(home, away, cache):
    if home not in cache:
        cache[home] = _fetch_recent_results(home)
    if away not in cache:
        cache[away] = _fetch_recent_results(away)
    hd = _build_team_signal(home, cache[home])
    ad = _build_team_signal(away, cache[away])
    h2h = _h2h(home, away, cache[home] + cache[away])
    sig = compute_match_signals(hd, ad)
    pick = best_market_pick(hd, ad, sig)
    # For 1X2 markets, layer in the legacy H2H/form fact-check
    if pick["market"] in ("Home", "Draw", "Away"):
        passed, reasons = fact_check_1x2(pick["market"], h2h, sig["home_form"], sig["away_form"])
        pick["fact_checked"] = pick["fact_checked"] and passed
        pick["reason"] = "; ".join([pick["reason"]] + reasons)
    low = hd["games_played"] == 0 or ad["games_played"] == 0
    if low:
        pick["confidence"] = round(min(pick["confidence"], 0.5), 3)
        pick["odds"] = _odds_from_prob(pick["confidence"])
        pick["reason"] += " ; ⚠️ Limited recent data — estimate only"
    pick["fixture"] = f"{home} vs {away}"
    return pick

# ---------------- AUTO DAILY BOARD ----------------
@st.cache_data(ttl=3600)
def daily_board():
    rows = []; picks = []
    for lg in LEAGUES:
        fixtures = _as_list((_api_get(API_BASE, "eventsnextleague.php", {"id": LEAGUE_IDS[lg]}) or {}).get("events"))
        cache = {}
        for fx in fixtures[:6]:
            home, away = fx.get("strHomeTeam", "?"), fx.get("strAwayTeam", "?")
            dd = fx.get("dateEvent") or ""
            pick = _analyze(home, away, cache)
            rows.append({"Date": dd, "League": lg, "Match": pick["fixture"],
                         "Pick": pick["market"], "Confidence": f"{pick['confidence']*100:.0f}%",
                         "Odds": f"{pick['odds']:.2f}", "Fact-check": "✅" if pick["fact_checked"] else "⚠️"})
            if pick["fact_checked"] and pick["confidence"] >= 0.55:
                p = pick.copy()
                p["league"] = lg
                picks.append(p)
    rows.sort(key=lambda r: (r["Date"] or "9999", -int(r["Confidence"][:-1])))
    picks.sort(key=lambda p: -p["confidence"])
    return rows, picks

# ---------------- TheOddsAPI (hidden) ----------------
def get_odds_api_key():
    key = os.environ.get("OWENZO_ODDS_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("OWENZO_ODDS_API_KEY", "")
        except Exception:
            key = ""
    return (key or "").strip()

def fetch_market_odds(api_key, sport_key, region="eu", market="h2h"):
    if not api_key:
        return []
    data = _api_get(ODDS_API_BASE, f"sports/{sport_key}/odds",
                    {"apiKey": api_key, "regions": region, "markets": market, "oddsFormat": "decimal"})
    out = []
    for ev in _as_list(data):
        if not isinstance(ev, dict):
            continue
        home, away = ev.get("home_team", ""), ev.get("away_team", "")
        if not home or not away:
            continue
        hodds = None
        for bm in _as_list(ev.get("bookmakers")):
            for mkt in _as_list(bm.get("markets")):
                if mkt.get("key") != "h2h":
                    continue
                for o in _as_list(mkt.get("outcomes")):
                    if o.get("name") == home:
                        hodds = _safe_float(o.get("price"), 0.0)
        if hodds and hodds > 1.0:
            out.append({"home": home, "away": away, "home_odds": hodds})
    return out

def market_implied_prob(odds):
    return 1.0 / odds if odds and odds > 1.0 else 0.0

# ---------------- slip builders ----------------
def build_slip(picks, bankroll=DEFAULT_BANKROLL, stake_pct=STAKE_PCT, max_legs=MAX_LEGS, max_odds=MAX_ODDS):
    legs = [p for p in picks if p.get("fact_checked")][:max_legs]
    if not legs:
        return None
    combined = 1.0
    for p in legs:
        combined *= _safe_float(p.get("odds", 1.0), 1.0)
        if combined > max_odds:
            legs = legs[:-1]
            combined = 1.0
            for q in legs:
                combined *= _safe_float(q.get("odds", 1.0), 1.0)
            break
    if not legs:
        return None
    stake = round(bankroll * stake_pct, 2)
    return {"legs": legs, "combined_odds": round(combined, 2), "stake": stake,
            "potential_return": round(stake * combined, 2),
            "avg_confidence": round(sum(_safe_float(p.get("confidence", 0)) for p in legs) / len(legs), 3)}

def build_daily_odds(picks, target=2.0, lo=1.8, hi=2.2):
    best = None
    for i in range(len(picks)):
        for j in range(i + 1, len(picks)):
            # prevent picking the same fixture twice
            if picks[i].get("fixture") == picks[j].get("fixture"):
                continue
            comb = _safe_float(picks[i].get("odds", 1.0)) * _safe_float(picks[j].get("odds", 1.0))
            if lo <= comb <= hi:
                avg_conf = (_safe_float(picks[i].get("confidence", 0)) + _safe_float(picks[j].get("confidence", 0))) / 2
                cand = (abs(comb - target), -avg_conf, i, j, comb, avg_conf)
                if best is None or cand < best:
                    best = cand
    if best is None:
        return None
    _, _, i, j, comb, avg_conf = best
    return {"legs": [picks[i], picks[j]], "combined_odds": round(comb, 2), "avg_confidence": round(avg_conf, 3)}

# ---------------- backtest ----------------
def fetch_historical_fixtures(league_id, season="2024-2025", limit=200):
    data = _api_get(API_BASE, "eventsseason.php", {"id": league_id, "s": season})
    out = []
    for ev in _as_list((data or {}).get("events")):
        if not isinstance(ev, dict):
            continue
        home, away = ev.get("strHomeTeam", ""), ev.get("strAwayTeam", "")
        hs, as_ = _safe_float(ev.get("intHomeScore"), -1), _safe_float(ev.get("intAwayScore"), -1)
        if home and away and hs >= 0 and as_ >= 0:
            out.append({"home": home, "away": away, "hs": hs, "as": as_})
    return out[:limit]

def _actual_result(hs, as_):
    if hs > as_:
        return "Home"
    elif as_ > hs:
        return "Away"
    return "Draw"

def _market_won(market, hs, as_):
    if market == "Home": return hs > as_
    if market == "Away": return as_ > hs
    if market == "Draw": return hs == as_
    if market == "Over 1.5": return (hs + as_) >= 2
    if market == "Over 2.5": return (hs + as_) >= 3
    if market == "BTTS": return hs >= 1 and as_ >= 1
    if market == "1X": return hs >= as_
    if market == "X2": return as_ >= hs
    if market == "12": return hs != as_
    return False

def run_backtest(fixtures, min_confidence=0.5):
    results = []; cache = {}
    for fx in fixtures:
        pick = _analyze(fx["home"], fx["away"], cache)
        if not pick["fact_checked"] or pick["confidence"] < min_confidence:
            continue
        won = _market_won(pick["market"], fx["hs"], fx["as"])
        results.append({"Fixture": pick["fixture"], "Pick": pick["market"],
                        "Actual": _actual_result(fx["hs"], fx["as"]),
                        "Odds": pick["odds"],
                        "Confidence": f"{pick['confidence']*100:.0f}%",
                        "Result": "W" if won else "L"})
    return results

def backtest_metrics(results):
    n = len(results)
    if n == 0:
        return None
    wins = sum(1 for r in results if r["Result"] == "W")
    avg_odds = sum(_safe_float(r["Odds"]) for r in results) / n
    pnl = sum(_safe_float(r["Odds"]) if r["Result"] == "W" else 0.0 for r in results) - n
    return {"bets": n, "wins": wins, "losses": n - wins, "hit_rate": round(wins / n, 4),
            "avg_odds": round(avg_odds, 2), "pnl": round(pnl, 2), "roi": round(pnl / n, 4) if n else 0.0}

# ================= UI =================
st.set_page_config(page_title="Owenzo Football AI", layout="wide")
_ensure_default_admin()
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
    st.session_state["username"] = None
if "settings" not in st.session_state:
    st.session_state["settings"] = load_settings()

st.title("⚽ Owenzo Football AI — Live Prediction Engine")
st.caption("Multi-market AI (1X2 / O1.5 / O2.5 / BTTS / Double Chance) + fact-check layer + real market-odds value feed. **Predictions are probabilistic model estimates — NOT guarantees.** Bet responsibly.")
if st.session_state["logged_in"]:
    st.caption(f"👤 Signed in as **{st.session_state['username']}**")
    if st.button("Logout"):
        st.session_state["logged_in"] = False
        st.session_state["username"] = None
        st.rerun()
else:
    st.caption("🔓 Tabs 1-3 are FREE • 🔒 Tabs 4-7 are **Owenzõ Soccer AI Vip-01** — sign in inside the tab")

def _vip_gate(key):
    if st.session_state["logged_in"]:
        return True
    st.warning("🔒 **Owenzõ Soccer AI Vip-01** — VIP area, sign in to access.")
    with st.form("login_" + key):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign In"):
            if authenticate(u, p):
                st.session_state["logged_in"] = True
                st.session_state["username"] = u
                st.rerun()
            else:
                st.error("Invalid username or password.")
    return False

tab_predict, tab_tracker, tab_euro, tab_value, tab_daily2, tab_bankroll, tab_backtest = st.tabs(
    ["Predict & Slips", "Tracker & ROI", "Euro Hub", "🔒 Value Bets", "🔒 Daily 2-Odds", "🔒 Bankroll", "🔒 Backtest"])

# ---- TAB 1 (PUBLIC, AUTO) ----
with tab_predict:
    st.subheader("🔮 Daily Prediction Board — All Leagues (auto, multi-market)")
    st.caption("Auto-generated every hour. Each fixture shows the **highest-confidence market** across 1X2, Over 1.5/2.5, BTTS and Double Chance. Slip auto-records once per day.")
    board_rows, board_picks = daily_board()
    if not board_rows:
        st.info("No upcoming fixtures found right now.")
    else:
        st.markdown(md_table(pd.DataFrame(board_rows)))
        _s = st.session_state["settings"]
        slip = build_slip(board_picks, bankroll=float(_s["bankroll"]), stake_pct=float(_s["stake_pct"]),
                          max_legs=int(_s["max_legs"]), max_odds=float(_s["max_odds"]))
        if slip and slip["legs"]:
            st.markdown("### 📋 Today's Auto Slip (best market per fixture)")
            st.markdown(md_table(pd.DataFrame([
                {"Leg": i + 1, "Fixture": p["fixture"], "Pick": p["market"],
                 "Odds": f"{p['odds']:.2f}", "Confidence": f"{p['confidence']*100:.0f}%"}
                for i, p in enumerate(slip["legs"])])))
            st.markdown(f"**Combined odds:** {slip['combined_odds']} | **Stake:** {slip['stake']} | **Potential return:** {slip['potential_return']} | **Avg confidence:** {slip['avg_confidence']*100:.0f}%")
            if auto_record_slip(slip):
                st.success("📥 Slip auto-recorded to Tracker & ROI.")
        else:
            st.info("No picks passed the fact-check threshold today.")
    st.markdown("---\n*Confidence is a model estimate capped at 78% — not a guarantee.*")

# ---- TAB 2 ----
with tab_tracker:
    st.subheader("📊 Tracker & ROI")
    df, has_date, date_col = load_slips()
    if df.empty:
        st.info("No graded slips found yet.")
    else:
        pnl = compute_pnl_summary(df, has_date)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Daily P/L", f"{pnl['daily']:+.2f}"); c2.metric("Weekly P/L", f"{pnl['weekly']:+.2f}")
        c3.metric("Monthly P/L", f"{pnl['monthly']:+.2f}"); c4.metric("Total P/L", f"{pnl['total']:+.2f}")
        c5, c6, c7 = st.columns(3)
        c5.metric("Slips", pnl["slips"]); c6.metric("Wins", pnl["wins"]); c7.metric("Losses", pnl["losses"])
        if pnl["has_date"]:
            st.markdown("### 📈 Recent Daily Trend (last 14 days)")
            st.markdown(md_table(pnl["trend"]))
        show = df.copy()
        if "_profit" in show.columns:
            show["P/L"] = show["_profit"].round(2)
        st.markdown("### All Slips")
        st.markdown(md_table(show))

# ---- TAB 3 ----
with tab_euro:
    st.subheader("🏆 Euro Hub")
    st.markdown("European competition context and fixtures.")

# ---- TAB 4 (VIP) ----
with tab_value:
    if _vip_gate("value"):
        st.subheader("👑 Owenzõ Soccer AI Vip-01 — 💎 Value Bets (Real Market Odds)")
        with st.expander("⚙️ Odds Settings"):
            st.caption("🔐 Live market odds feed: **active** — API key stored privately by the owner (Streamlit Secrets) and never displayed.")
            region = st.selectbox("Odds region", ["eu", "uk", "us"], index=0)
        with st.expander("💰 Bankroll & Betting Settings"):
            _s = st.session_state["settings"]
            stake_pct = st.slider("Stake % of bankroll", 0.1, 5.0, float(_s["stake_pct"]) * 100, 0.1) / 100.0
            edge_thr = st.slider("Edge threshold", 0.01, 0.20, float(_s["edge_threshold"]), 0.01)
            bankroll = st.number_input("Starting bankroll", min_value=10.0, value=float(_s["bankroll"]), step=50.0)
            max_legs = st.slider("Max legs per slip", 1, 8, int(_s["max_legs"]))
            max_odds = st.slider("Max combined odds", 1.5, 20.0, float(_s["max_odds"]), 0.5)
            if st.button("Save Betting Settings"):
                st.session_state["settings"] = {"stake_pct": stake_pct, "edge_threshold": edge_thr,
                                                "bankroll": bankroll, "max_legs": max_legs, "max_odds": max_odds}
                for k, v in st.session_state["settings"].items():
                    save_setting(k, v)
                st.success("Betting settings saved.")
        value_league = st.selectbox("League (odds)", LEAGUES, key="value_league")
        api_key = get_odds_api_key()
        if not api_key:
            st.warning("Odds feed not configured on server. Falling back to estimated odds.")
        if st.button("Find Value Bets", type="primary"):
            market = fetch_market_odds(api_key, ODDS_SPORT_KEYS.get(value_league, "soccer_epl"), region=region) if api_key else []
            fixtures = _as_list((_api_get(API_BASE, "eventsnextleague.php", {"id": LEAGUE_IDS[value_league]}) or {}).get("events"))
            if not market:
                st.info("No real market odds available. Showing model-estimated odds instead.")
            rows = []; picks = []; cache = {}
            for fx in fixtures[:12]:
                home, away = fx.get("strHomeTeam", "?"), fx.get("strAwayTeam", "?")
                pick = _analyze(home, away, cache)
                # Only compare value on Home 1X2 (what market odds cover)
                if pick["market"] == "Home":
                    market_odds = next((m["home_odds"] for m in market
                                        if m["home"].lower() == home.lower() and m["away"].lower() == away.lower()), None)
                    if market_odds:
                        market_prob = market_implied_prob(market_odds)
                        edge = pick["confidence"] - market_prob
                        is_value = edge >= float(st.session_state["settings"]["edge_threshold"])
                        rows.append({"Fixture": pick["fixture"], "Best market": pick["market"],
                                     "Model prob": f"{pick['confidence']*100:.0f}%",
                                     "Market odds": f"{market_odds:.2f}",
                                     "Market implied": f"{market_prob*100:.0f}%",
                                     "Edge": f"{edge*100:+.1f}%",
                                     "Value": "✅" if is_value else "—"})
                        if is_value and pick["fact_checked"]:
                            picks.append(pick)
                        continue
                rows.append({"Fixture": pick["fixture"], "Best market": pick["market"],
                             "Model prob": f"{pick['confidence']*100:.0f}%",
                             "Market odds": f"{pick['odds']:.2f} (est)",
                             "Market implied": f"{market_implied_prob(pick['odds'])*100:.0f}%",
                             "Edge": "—", "Value": "—"})
                if pick["fact_checked"]:
                    picks.append(pick)
            if rows:
                st.markdown("### Value Analysis (best market per fixture)")
                st.markdown(md_table(pd.DataFrame(rows)))
                _s = st.session_state["settings"]
                slip = build_slip(picks, bankroll=float(_s["bankroll"]), stake_pct=float(_s["stake_pct"]),
                                  max_legs=int(_s["max_legs"]), max_odds=float(_s["max_odds"]))
                if slip and slip["legs"]:
                    st.markdown("### 💰 Value Slip")
                    st.markdown(md_table(pd.DataFrame([
                        {"Leg": i + 1, "Fixture": p["fixture"], "Pick": p["market"],
                         "Odds": f"{p['odds']:.2f}", "Confidence": f"{p['confidence']*100:.0f}%"}
                        for i, p in enumerate(slip["legs"])])))
                    st.markdown(f"**Combined odds:** {slip['combined_odds']} | **Stake:** {slip['stake']} | **Potential return:** {slip['potential_return']}")
                else:
                    st.info("No value bets found above the edge threshold today.")
        st.markdown("---\n*Value betting improves expected value over large samples — it does not guarantee wins.*")

# ---- TAB 5 (VIP, MULTI-LEAGUE, MULTI-MARKET) ----
with tab_daily2:
    if _vip_gate("daily2"):
        st.subheader("👑 Owenzõ Soccer AI Vip-01 — 🎯 Daily 2-Odds (All Leagues × All Markets)")
        st.caption("Scans ALL leagues and picks the **2 highest-confidence markets** (1X2 / O1.5 / O2.5 / BTTS / DC) whose combined odds land closest to 2.0 (1.8–2.2). Legs can come from different leagues.")
        if st.button("Build Daily 2-Odds", type="primary"):
            with st.spinner("Scanning ALL leagues for the best 2-market combination..."):
                candidates = []; cache = {}
                for lg in LEAGUES:
                    fixtures = _as_list((_api_get(API_BASE, "eventsnextleague.php", {"id": LEAGUE_IDS[lg]}) or {}).get("events"))
                    for fx in fixtures[:8]:
                        home, away = fx.get("strHomeTeam", "?"), fx.get("strAwayTeam", "?")
                        pick = _analyze(home, away, cache)
                        if not pick["fact_checked"] or pick["confidence"] < 0.5:
                            continue
                        pick["league"] = lg
                        candidates.append(pick)
                candidates.sort(key=lambda p: -p["confidence"])
            if not candidates:
                st.info("No qualifying picks today. Try again later.")
            else:
                d2 = build_daily_odds(candidates)
                if d2:
                    _s = st.session_state["settings"]
                    stake = round(float(_s["bankroll"]) * float(_s["stake_pct"]), 2)
                    st.markdown("### 🎯 Today's 2-Odds Accumulator")
                    st.markdown(md_table(pd.DataFrame([
                        {"Leg": i + 1, "League": p.get("league", ""), "Fixture": p["fixture"],
                         "Market": p["market"], "Odds": f"{p['odds']:.2f}",
                         "Confidence": f"{p['confidence']*100:.0f}%", "Reason": p["reason"]}
                        for i, p in enumerate(d2["legs"])])))
                    st.markdown(f"**Combined odds:** {d2['combined_odds']:.2f} | **Avg confidence:** {d2['avg_confidence']*100:.0f}% | **Stake:** {stake} | **Potential return:** {stake * d2['combined_odds']:.2f}")
                else:
                    st.info("No pair landed in the 1.8–2.2 range today.")
        st.markdown("---\n*A ~2.0 accumulator can lose. Bet responsibly.*")

# ---- TAB 6 (VIP) ----
with tab_bankroll:
    if _vip_gate("bankroll"):
        st.subheader("👑 Owenzõ Soccer AI Vip-01 — 💰 Bankroll Growth")
        stats = bankroll_stats()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current bankroll", f"{stats['current']:.2f}")
        c2.metric("Total P/L", f"{stats['total_pnl']:+.2f}")
        c3.metric("Win rate", f"{stats['win_rate']*100:.1f}%")
        c4.metric("Bets", stats["bets"])
        bets = load_bets()
        if bets:
            df_bets = pd.DataFrame(bets)
            if "running_balance" in df_bets.columns:
                chart_df = df_bets[["timestamp", "running_balance"]].copy()
                chart_df["timestamp"] = pd.to_datetime(chart_df["timestamp"], errors="coerce")
                chart_df = chart_df.dropna(subset=["timestamp"]).sort_values("timestamp")
                st.markdown("### 📈 Bankroll growth over time")
                st.line_chart(chart_df.set_index("timestamp")["running_balance"])
            st.markdown("### All bets")
            st.markdown(md_table(df_bets))
        else:
            st.info("No bets recorded yet. Use the form below to add a bet.")
        st.markdown("### ➕ Record a bet")
        with st.form("record_bet_form"):
            b_fixture = st.text_input("Fixture (e.g. Arsenal vs Chelsea)")
            b_market = st.selectbox("Market", ["Home", "Draw", "Away", "Over 1.5", "Over 2.5", "BTTS", "1X", "X2", "12"])
            b_odds = st.number_input("Odds", min_value=1.01, value=2.0, step=0.1)
            _s = st.session_state["settings"]
            b_stake = st.number_input("Stake", min_value=0.0, value=round(float(_s["bankroll"]) * float(_s["stake_pct"]), 2), step=1.0)
            b_result = st.selectbox("Result", ["pending", "win", "loss"])
            b_submit = st.form_submit_button("Add bet")
        if b_submit:
            if b_fixture:
                bet_id = record_bet(st.session_state["username"], b_fixture, b_market, b_odds, b_stake, b_result)
                st.success(f"Bet recorded (id {bet_id}).")
                st.rerun()
            else:
                st.warning("Enter a fixture name.")

# ---- TAB 7 (VIP) ----
with tab_backtest:
    if _vip_gate("backtest"):
        st.subheader("👑 Owenzõ Soccer AI Vip-01 — 🧪 Backtest Mode")
        st.caption("Simulate the multi-market strategy on historical fixtures. Needs 500–1,000+ bets to be statistically meaningful. Past performance ≠ future results.")
        bt_league = st.selectbox("League (backtest)", LEAGUES, key="bt_league")
        bt_season = st.text_input("Season (e.g. 2024-2025)", value="2024-2025")
        bt_limit = st.slider("Max fixtures to backtest", 20, 300, 100, 10)
        if st.button("Run Backtest", type="primary"):
            with st.spinner("Fetching historical fixtures & simulating..."):
                fixtures = fetch_historical_fixtures(LEAGUE_IDS[bt_league], bt_season, bt_limit)
            if not fixtures:
                st.info("No historical fixtures found for this league/season. Try another season.")
            else:
                results = run_backtest(fixtures)
                if not results:
                    st.info("No picks met the confidence/fact-check bar in this sample.")
                else:
                    m = backtest_metrics(results)
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Bets", m["bets"]); c2.metric("Hit rate", f"{m['hit_rate']*100:.1f}%")
                    c3.metric("ROI (flat 1u)", f"{m['roi']*100:+.1f}%"); c4.metric("Avg odds", f"{m['avg_odds']:.2f}")
                    c5.metric("P/L (flat 1u)", f"{m['pnl']:+.2f}")
                    st.markdown("### Sample backtested bets")
                    st.markdown(md_table(pd.DataFrame(results[:20])))

# ---- Account & Admin ----
if st.session_state["logged_in"]:
    with st.expander("🔐 My Account"):
        st.markdown("Change your own password (requires current password).")
        my_cur = st.text_input("Current password", type="password", key="my_cur_pass")
        my_new = st.text_input("New password", type="password", key="my_new_pass")
        my_new2 = st.text_input("Confirm new password", type="password", key="my_new_pass2")
        if st.button("Change my password"):
            if my_new != my_new2:
                st.error("New passwords do not match.")
            elif len(my_new) < 6:
                st.error("New password must be at least 6 characters.")
            else:
                ok, msg = change_password(st.session_state["username"], my_cur, my_new)
                st.success(msg) if ok else st.error(msg)

    if is_admin(st.session_state.get("username")):
        with st.expander("🔐 Private Admin Settings (owner only)"):
            st.markdown("Generate access passwords for VIP users.")
            new_username = st.text_input("New username", key="admin_new_user")
            gen_pass = st.text_input("Generated password", value=st.session_state.get("gen_pass", ""), key="admin_gen_pass")
            cg1, cg2 = st.columns(2)
            if cg1.button("Generate strong password"):
                st.session_state["gen_pass"] = generate_strong_password()
                st.rerun()
            if cg2.button("Add user"):
                if new_username and gen_pass:
                    st.success(f"User '{new_username}' added.") if add_user(new_username, gen_pass) else st.error("Username already exists.")
                else:
                    st.warning("Provide both a username and a password.")
            st.markdown("### Current users")
            users = list_users()
            if users:
                st.markdown(md_table(pd.DataFrame(users)))
                revoke_options = [u["username"] for u in users if u["username"] != DEFAULT_ADMIN_USER]
                if revoke_options:
                    revoke_name = st.selectbox("Revoke user", revoke_options)
                    if st.button("Revoke selected user"):
                        if revoke_user(revoke_name):
                            st.success(f"Revoked '{revoke_name}'.")
                            st.rerun()
                else:
                    st.caption("No other users yet — add your first VIP member above.")
            if users:
                st.markdown("### 🔑 Change another user's password (admin override)")
                target_user = st.selectbox("User", [u["username"] for u in users], key="target_reset_user")
                reset_pass = st.text_input("New password for user", type="password", key="admin_reset_pass")
                if st.button("Set user password"):
                    if len(reset_pass) < 6:
                        st.error("New password must be at least 6 characters.")
                    else:
                        admin_set_password(target_user, reset_pass)
                        st.success(f"Password updated for '{target_user}'.")

st.markdown("---\n**Disclaimer:** probabilistic model estimates for entertainment/analysis only. Not financial or betting advice. Only bet what you can afford to lose.")