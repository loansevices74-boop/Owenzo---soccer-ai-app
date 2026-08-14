# engine_v7.py - upgraded prediction engine
# v7 = recency-decay form + Glicko-1 dynamic ratings + bivariate draw boost
#      + variance filter + value filter (closing-line proxy)
import math
import json
import requests
from urllib.parse import quote

SPORTSDB_KEYS = ["3", "123"]
FORM_CACHE = "form_cache_v7.json"
RHO = -0.07
HOME_SHARE = 0.55

LEAGUE_GOALS = {
    "Argentina": 2.40, "Ireland": 2.60, "USA": 2.90, "England": 2.75,
    "Spain": 2.60, "Italy": 2.55, "Germany": 3.05, "Netherlands": 3.10,
    "Norway": 3.15, "Sweden": 2.95, "Finland": 2.75, "Brazil": 2.55,
    "Mexico": 2.70, "International Clubs": 2.70,
}

LEAGUE_ALIAS = {
    "veikkausliiga": "Finland", "allsvenskan": "Sweden",
    "eliteserien": "Norway", "leagues cup": "USA", "mls": "USA",
    "liga mx": "Mexico", "brasileirao serie a": "Brazil",
    "copa santa catarina": "Brazil", "la liga": "Spain",
    "uefa champions league": "International Clubs",
    "uefa europa league": "International Clubs",
    "uefa conference league": "International Clubs",
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

DECAY = [1.0, 0.85, 0.72, 0.61, 0.52, 0.44]

try:
    with open(FORM_CACHE) as _f:
        CACHE = json.load(_f)
except Exception:
    CACHE = {}


def league_country(league):
    l = (league or "").lower()
    if l in LEAGUE_ALIAS:
        return LEAGUE_ALIAS[l]
    for sub, c in SUBSTR_ALIAS:
        if sub in l:
            return c
    return "International"


def _api(path):
    for k in SPORTSDB_KEYS:
        try:
            r = requests.get("https://www.thesportsdb.com/api/v1/json/" + k + "/" + path, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None


def _recent(name):
    data = _api("searchteams.php?t=" + quote(name))
    teams = (data or {}).get("teams")
    if not teams:
        return []
    tid = teams[0].get("idTeam")
    ev = _api("eventslast.php?id=" + str(tid))
    out = []
    for e in ((ev or {}).get("results") or [])[:6]:
        fh, fa = e.get("intHomeScore"), e.get("intAwayScore")
        if fh is None or fa is None:
            continue
        gf, ga = (int(fh), int(fa)) if e.get("strHomeTeam") == name else (int(fa), int(fh))
        out.append((gf, ga))
    return out


def _glicko(results):
    R, RD = 1500.0, 170.0
    for gf, ga in reversed(results):
        s = 1.0 if gf > ga else (0.5 if gf == ga else 0.0)
        g = 1.0 / math.sqrt(1 + 3 * RD * RD / (math.pi * math.pi))
        E = 1.0 / (1 + 10 ** (-(R - 1500.0) * g / 400.0))
        v = 1.0 / (g * g * E * (1 - E) + 1e-9)
        RD = math.sqrt(1.0 / (1.0 / (RD * RD) + 1.0 / v))
        mov = min(1.5, math.log(abs(gf - ga) + 1.0) + 0.6)
        R = R + RD * RD * g * (s - E) * mov
    return R, RD


def team_v7(name):
    c = CACHE.get(name)
    if isinstance(c, dict) and "rating" in c:
        return c
    res = _recent(name)
    if not res:
        return {"rating": 1500.0, "rd": 170.0, "att": 1.35, "defn": 1.35, "vol": 1.0, "n": 0}
    R, RD = _glicko(res)
    wsum = gf_w = ga_w = 0.0
    nets = []
    for (gf, ga), w in zip(res, DECAY):
        gf_w += gf * w
        ga_w += ga * w
        wsum += w
        nets.append(gf - ga)
    mean = sum(nets) / len(nets)
    vol = math.sqrt(sum((x - mean) ** 2 for x in nets) / len(nets))
    prof = {"rating": R, "rd": RD, "att": gf_w / wsum, "defn": ga_w / wsum,
            "vol": round(vol, 2), "n": len(res)}
    CACHE[name] = prof
    try:
        with open(FORM_CACHE, "w") as _f:
            json.dump(CACHE, _f)
    except Exception:
        pass
    return prof


def _pois(lam, n=10):
    p = [math.exp(-lam) * lam ** i / math.factorial(i) for i in range(n + 1)]
    s = sum(p)
    return [x / s for x in p]


def _tau(i, j, lh, la, rho):
    if i == 0 and j == 0:
        return 1.0 - lh * la * rho
    if i == 0 and j == 1:
        return 1.0 + lh * rho
    if i == 1 and j == 0:
        return 1.0 + la * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def model_match(home, away, league):
    country = league_country(league)
    hp, ap = team_v7(home), team_v7(away)
    base = LEAGUE_GOALS.get(country, 2.70)

    def shrink(x, n):
        K = 3.0
        return (x * n + 1.35 * K) / (n + K)

    h_att, h_def = shrink(hp["att"], hp["n"]), shrink(hp["defn"], hp["n"])
    a_att, a_def = shrink(ap["att"], ap["n"]), shrink(ap["defn"], ap["n"])
    tilt = max(-0.35, min(0.35, (hp["rating"] - ap["rating"]) / 1200.0))

    def clampf(x):
        return max(0.4, min(2.2, x))

    lh = base * HOME_SHARE * clampf(h_att / 1.35) * clampf(a_def / 1.35) * (1 + tilt)
    la = base * (1 - HOME_SHARE) * clampf(a_att / 1.35) * clampf(h_def / 1.35) * (1 - tilt)
    lh, la = max(0.2, min(3.6, lh)), max(0.2, min(3.6, la))

    ph, pa = _pois(lh), _pois(la)
    mat = [[ph[i] * pa[j] * _tau(i, j, lh, la, RHO) for j in range(11)] for i in range(11)]
    tot = sum(sum(r) for r in mat)
    mat = [[mat[i][j] / tot for j in range(11)] for i in range(11)]

    p_home = sum(mat[i][j] for i in range(11) for j in range(11) if i > j)
    p_draw = sum(mat[i][i] for i in range(11))
    p_away = 1 - p_home - p_draw

    balance = 1 - abs(p_home - p_away)
    low = 1.0 if (lh + la) < 2.4 else 0.5
    p_draw *= (1 + 0.25 * balance * low)
    s = p_home + p_draw + p_away
    p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

    p_over = sum(mat[i][j] for i in range(11) for j in range(11) if i + j >= 3)
    p_btts = sum(mat[i][j] for i in range(1, 11) for j in range(1, 11))

    bi, bj, bp = 1, 1, -1.0
    for i in range(6):
        for j in range(6):
            if mat[i][j] > bp:
                bp, bi, bj = mat[i][j], i, j

    volatile = hp["vol"] > 1.4 or ap["vol"] > 1.4 or hp["rd"] > 120 or ap["rd"] > 120

    proxy_home = 1 / (1 + 10 ** (-(hp["rating"] - ap["rating"] + 60) / 400.0))
    neutral = _pois(base * HOME_SHARE)
    neutral_over = sum(neutral[i] for i in range(4, 11))

    return {
        "league": league, "country": country, "home": home, "away": away,
        "xg_home": lh, "xg_away": la,
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_over25": p_over, "p_under25": 1 - p_over,
        "p_btts_yes": p_btts, "p_btts_no": 1 - p_btts,
        "expected_score": "%d - %d" % (bi, bj),
        "volatile": volatile,
        "v_home": p_home > proxy_home + 0.03,
        "v_over": p_over > neutral_over + 0.03,
        "v_btts": abs(p_btts - 0.5) > 0.06,
        "form_home": "R%d vol%.1f" % (hp["rating"], hp["vol"]),
        "form_away": "R%d vol%.1f" % (ap["rating"], ap["vol"]),
    }


def generate_legs(pred):
    legs = []
    if pred["volatile"]:
        return legs

    def add(market, sel, p, value):
        if p > 0.55 and value:
            legs.append({
                "country": pred["country"], "league": pred["league"],
                "home": pred["home"], "away": pred["away"],
                "match": pred["home"] + " vs " + pred["away"],
                "market": market, "selection": sel, "prob": round(p, 3),
                "odds": round(1.0 / p, 2), "expected_score": pred["expected_score"],
                "xg": "%.2f - %.2f" % (pred["xg_home"], pred["xg_away"]),
                "form": pred["form_home"] + " | " + pred["form_away"],
                "value": True,
            })

    add("1X2", pred["home"] + " Win", pred["p_home"], pred["v_home"])
    add("1X2", "Draw", pred["p_draw"], pred["p_draw"] > 0.30)
    add("1X2", pred["away"] + " Win", pred["p_away"], pred["p_away"] > pred["p_home"] and pred["v_home"] is False and pred["p_away"] > 0.55)
    add("Over/Under 2.5", "Over 2.5", pred["p_over25"], pred["v_over"])
    add("Over/Under 2.5", "Under 2.5", pred["p_under25"], pred["p_under25"] > 0.60)
    add("BTTS", "BTTS Yes", pred["p_btts_yes"], pred["v_btts"] and pred["p_btts_yes"] > 0.55)
    add("BTTS", "BTTS No", pred["p_btts_no"], pred["v_btts"] and pred["p_btts_no"] > 0.55)
    return legs
