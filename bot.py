bot.py — Telegram Auto-Post Bot
  Daily slip 06:00 every day | VIP slip Friday 06:00 | Results Sunday 21:00
"""
import os
import time
import argparse
import schedule
import requests
from datetime import date, datetime, timedelta

import engine
import tracker

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
CHAT_ID = os.environ.get("TG_CHAT_ID", "PASTE_CHAT_ID_HERE")
BANKROLL = float(os.environ.get("BANKROLL", "1000"))

tracker.init_db()


def tg_send(text):
    if BOT_TOKEN.startswith("PASTE_"):
        print("[DRY RUN — no token]\n", text)
        return {"ok": True}
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text,
              "disable_web_page_preview": True}, timeout=20).json()


def send_long(text, limit=4000):
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        tg_send(text[:cut])
        text = text[cut + 1:]
    tg_send(text)


def fmt_leg(l):
    return (f"{l.get('country', '—')} | {l['league']}\n{l['match']}\n"
            f"Pick: {l['market']} - {l['selection']} @ {l['odds']}\n"
            f"Exp. score: {l['expected_score']} | AI prob: "
            f"{int(l['prob'] * 100)}%\n")


def fmt_slip(title, slip, total, stake):
    lines = [title, f"Total AI odds: {total} | Stake: {stake}", "-" * 34, ""]
    lines += [fmt_leg(l) for l in slip]
    lines += ["-" * 34,
              "Booking odds neglected. AI Fair Odds only. Paper-trade first."]
    return "\n".join(lines)


def build_slips(days=1):
    fixtures, _ = engine.get_fixtures(date.today(), days)
    legs = []
    for f in fixtures:
        legs += engine.generate_legs(
            engine.model_match(f["home"], f["away"], f["league"]))
    return legs


def post_daily():
    slip, tot = engine.build_accumulator(build_slips(1), 8, 4)
    if not slip:
        tg_send(f"📭 DAILY {date.today()}: no verified value legs today.")
        return
    stake = round(BANKROLL * 0.005, 2)
    send_long(fmt_slip(f"⚽ DAILY MIXED ACCUMULATOR — {date.today()}", slip, tot, stake))
    tracker.log_slip("DAILY", slip, tot, stake, str(date.today()))


def post_vip():
    slip, tot = engine.build_vip(build_slips(7))
    if not slip:
        tg_send(f"👑 VIP {date.today()}: not enough verified fixtures.")
        return
    stake = round(BANKROLL * 0.001, 2)
    send_long(fmt_slip(f"👑 VIP WEEKLY ~200-ODDS ACCUMULATOR — {date.today()}",
                       slip, tot, stake))
    tracker.log_slip("VIP", slip, tot, stake, str(date.today()))


def post_results():
    tracker.auto_grade_from_feed(engine)
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    rows = tracker.conn().execute(
        "SELECT slip_type, fixture_date, total_odds, stake, status, profit"
        " FROM slips WHERE settled_at >= ? ORDER BY slip_id", (cutoff,)).fetchall()

    lines = [f"📊 WEEKLY GRADED RESULTS — {date.today()}", "-" * 34]
    staked = ret = 0.0
    if not rows:
        lines.append("No settled slips this week.")
    for t, d, odds, stake, status, profit in rows:
        staked += stake
        ret += stake + profit
        lines.append(f"{'✅' if status == 'WON' else '❌'} {t} | {d} | "
                     f"odds {odds} | stake {stake} | P/L {profit}")
    profit = round(ret - staked, 2)
    lines += ["-" * 34, f"Staked: {round(staked, 2)} | Returned: {round(ret, 2)}",
              f"Profit: {profit} | "
              f"ROI: {round(100 * profit / staked, 1) if staked else 0.0}%"]
    for label, field in [("🌍 TOP COUNTRIES", "country"), ("🏟 TOP LEAGUES", "league")]:
        g = tracker.group_hit_rate(field)[:5]
        if g:
            lines.append(f"\n{label} (leg hit-rate):")
            lines += [f"• {r['grp']}: {r['hit_rate']}% ({r['legs']} legs)" for r in g]
    pend = len(tracker.pending_legs())
    if pend:
        lines.append(f"\n⏳ {pend} legs still pending.")
    send_long("\n".join(lines))


def main_loop():
    schedule.every().day.at("06:00").do(post_daily)
    schedule.every().friday.at("06:00").do(post_vip)
    schedule.every().sunday.at("21:00").do(post_results)
    print("Bot live: daily 06:00 | VIP Fri 06:00 | results Sun 21:00")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", choices=["daily", "vip", "results"])
    args = ap.parse_args()
    {"daily": post_daily, "vip": post_vip,
     "results": post_results}.get(args.now, main_loop)()
