app.py — Soccer AI Prediction Web App. Run: streamlit run app.py
"""
import streamlit as st
import pandas as pd
from datetime import datetime

import engine
import tracker

tracker.init_db()

st.set_page_config(page_title="Soccer AI Predictor", page_icon="⚽", layout="wide")
st.title("⚽ Soccer AI Prediction App")
st.caption("Booking odds **neglected** • Fixtures **fact-checked live** • "
           "**League + expected score** always shown • Simulated games removed")

with st.sidebar:
    st.header("🎛 Controls")
    start_date = st.date_input("Start date (from the 7th)",
                               datetime(2026, 8, 7).date())
    days = st.slider("Window (days)", 1, 10, 7)
    bankroll = st.number_input("Bankroll", 100, 100000, 1000, step=100)
    coverage = st.multiselect("Coverage universe (your menu)",
                              engine.WORKING_COUNTRIES,
                              default=engine.WORKING_COUNTRIES)

tab_pred, tab_track = st.tabs(["⚽ Predict & Slips", "📈 Tracker & ROI Dashboard"])

# ============================ PREDICT TAB ============================
with tab_pred:
    if st.button("🔄 Fetch, Fact-Check & Predict", type="primary"):
        fixtures, report = engine.get_fixtures(start_date, days)
        fixtures = [f for f in fixtures if f["country"] in coverage]

        all_legs, rows = [], []
        for f in fixtures:
            pred = engine.model_match(f["home"], f["away"], f["league"])
            all_legs += engine.generate_legs(pred)
            rows.append({
                "Date": f["date"], "Country": f["country"], "League": f["league"],
                "Match": f"{f['home']} vs {f['away']}",
                "Exp. Score": pred["expected_score"],
                "xG (H-A)": f"{pred['xg_home']:.2f} - {pred['xg_away']:.2f}",
                "P(Home)": f"{pred['p_home']:.0%}", "P(Draw)": f"{pred['p_draw']:.0%}",
                "P(Away)": f"{pred['p_away']:.0%}",
                "P(Over2.5)": f"{pred['p_over25']:.0%}",
                "P(BTTS)": f"{pred['p_btts_yes']:.0%}",
            })

        st.session_state["report"] = report
        st.session_state["rows"] = rows
        st.session_state["nfix"] = len(fixtures)
        st.session_state["slips"] = {
            "DAILY": engine.build_accumulator(all_legs, 8, 4),
            "WEEKLY": engine.build_accumulator(all_legs, 25, 7),
            "VIP": engine.build_vip(all_legs),
        }

    if "slips" in st.session_state:
        st.header("🔎 Fact-Check Report")
        st.dataframe(pd.DataFrame(st.session_state["report"]),
                     use_container_width=True, hide_index=True)
        st.success(f"{st.session_state['nfix']} verified fixtures loaded "
                   f"(simulated removed).")

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
            c1.metric("Total AI Odds", f"{tot:.2f}")
            c2.metric("Legs", len(slip))
            c3.metric("Suggested Stake", f"{stake} ({stakes[key]:.2%})")
            st.dataframe(pd.DataFrame([{
                "Country": l["country"], "League": l["league"],
                "Match": l["match"], "Market": l["market"],
                "Selection": l["selection"], "Prob": f"{l['prob']:.0%}",
                "AI Fair Odds": l["odds"], "Exp. Score": l["expected_score"],
            } for l in slip]), use_container_width=True, hide_index=True)
            with st.expander("🤖 AI analysis for every leg"):
                for l in slip:
                    st.markdown(engine.ai_analysis(l))

        if st.button("💾 Log these slips to tracker"):
            for key in ("DAILY", "WEEKLY", "VIP"):
                slip, tot = st.session_state["slips"][key]
                if slip:
                    tracker.log_slip(key, slip, tot,
                                     round(bankroll * stakes[key], 2),
                                     str(start_date))
            st.success("Logged to soccer_tracker.db")

# ============================ TRACKER TAB ============================
with tab_track:
    st.header("📈 Results Tracker & ROI Dashboard")

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🔄 Auto-grade from live feed"):
            st.success(f"{tracker.auto_grade_from_feed(engine)} legs auto-graded.")
    with c2:
        st.caption("HT-goals & corner legs need manual entry (feed lacks that data).")

    o = tracker.overall_stats()
    m = st.columns(5)
    m[0].metric("Staked", o["total_staked"])
    m[1].metric("Profit", o["profit"])
    m[2].metric("ROI", f"{o['roi']}%")
    m[3].metric("Slips Settled", o["slips_settled"])
    m[4].metric("Slip Hit Rate", f"{o['slip_hit_rate']}%")

    curve = tracker.profit_curve()
    if curve:
        dfc = pd.DataFrame(curve, columns=["when", "profit"])
        dfc["cum_profit"] = dfc.profit.cumsum()
        st.subheader("💰 Cumulative Profit Curve")
        st.line_chart(dfc.set_index("when")["cum_profit"])

    st.subheader("🎯 Hit-rate per Country / League / Market")
    g1, g2, g3 = st.columns(3)
    for col, field in [(g1, "country"), (g2, "league"), (g3, "market")]:
        rowsg = tracker.group_hit_rate(field)
        if rowsg:
            dfg = pd.DataFrame(rowsg)
            col.bar_chart(dfg.set_index("grp")["hit_rate"])
            col.dataframe(dfg, hide_index=True)

    st.subheader("✍️ Manual result entry")
    pend = tracker.pending_legs()
    if pend:
        opts = {f"#{r['leg_id']} [{r['slip_type']}] {r['match']} — "
                f"{r['selection']}": r for r in pend}
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
            st.info(f"Leg graded: **{tracker.enter_result(r['leg_id'], fh, fa, ht, corners)}**")
    else:
        st.info("No pending legs.")

    st.subheader("📒 Slip log")
    slips = tracker.all_slips()
    if slips:
        st.dataframe(pd.DataFrame(slips), use_container_width=True, hide_index=True)

st.divider()
st.caption("⚠️ Paper-trade first. This app never reads bookmaker odds; "
           "all prices are internal AI Fair Odds.")
