"""
Sahayak Caregiver Dashboard — Streamlit app.

Run: streamlit run dashboard/app.py
Backend must be running at SAHAYAK_BACKEND_URL (default: http://localhost:8000).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("SAHAYAK_BACKEND_URL", "http://localhost:8000")
REFRESH_INTERVAL = 30  # seconds between auto-refresh

st.set_page_config(
    page_title="Sahayak — Caregiver Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def api(method: str, path: str, **kwargs) -> Any | None:
    try:
        r = requests.request(method, f"{BACKEND_URL}{path}", timeout=10, **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot reach backend. Make sure `uvicorn main:app` is running.")
        return None
    except Exception as exc:
        st.warning(f"API error: {exc}")
        return None


def severity_badge(severity: str) -> str:
    colors = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    return colors.get(severity, "⚪")


def time_ago(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(tz=timezone.utc) - dt
        if delta.total_seconds() < 60:
            return "just now"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds()//60)}m ago"
        if delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds()//3600)}h ago"
        return f"{delta.days}d ago"
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🧠 Sahayak")
    st.caption("Caregiver Dashboard")

    user_id = st.text_input("Patient User ID", value="default_user", key="user_id")
    st.divider()

    auto_refresh = st.toggle("Auto-refresh (30s)", value=False)
    if st.button("Refresh Now", use_container_width=True):
        st.rerun()

    st.divider()
    health = api("GET", "/")
    if health:
        st.success(f"Backend online — uptime {int(health.get('uptime_seconds', 0))}s")
    else:
        st.error("Backend offline")

    st.divider()
    st.caption(f"Backend: {BACKEND_URL}")
    st.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

if auto_refresh:
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# ---------------------------------------------------------------------------
# Page tabs
# ---------------------------------------------------------------------------

tab_overview, tab_memories, tab_anomalies, tab_routine, tab_eval, tab_fl = st.tabs(
    ["Overview", "Memories", "Alerts", "Routine", "Evaluation", "Federated Learning"]
)

# ===========================================================================
# TAB 1 — OVERVIEW
# ===========================================================================

with tab_overview:
    st.header(f"Patient Overview — {user_id}")

    col_stats, col_anomaly_sum = st.columns([2, 1])

    with col_stats:
        memory_stats = api("GET", f"/memory/{user_id}/stats")
        if memory_stats:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Memories", memory_stats.get("count", 0))
            c2.metric("People Known", memory_stats.get("total_people", 0))
            oldest = memory_stats.get("oldest", "—")
            newest = memory_stats.get("newest", "—")
            c3.metric("Oldest Memory", time_ago(oldest) if oldest != "—" else "—")
            c4.metric("Latest Activity", time_ago(newest) if newest != "—" else "—")

    with col_anomaly_sum:
        severity_data = api("GET", f"/anomaly/{user_id}/status")
        if severity_data:
            high = severity_data.get("high", 0)
            medium = severity_data.get("medium", 0)
            low = severity_data.get("low", 0)
            if high > 0:
                st.error(f"🔴 {high} high-severity alert{'s' if high > 1 else ''}")
            if medium > 0:
                st.warning(f"🟡 {medium} medium-severity alert{'s' if medium > 1 else ''}")
            if low > 0:
                st.info(f"🟢 {low} low-severity alert{'s' if low > 1 else ''}")
            if high == medium == low == 0:
                st.success("✅ No active alerts")

    st.divider()

    st.subheader("Recent Memories (Last 24h)")
    recent = api("GET", f"/memory/{user_id}/recent", params={"hours": 24, "limit": 10})
    if recent:
        for chunk in recent:
            ts = time_ago(chunk.get("timestamp", ""))
            people = chunk.get("people", [])
            text = chunk.get("text", "")
            people_str = f" · 👤 {', '.join(people)}" if people else ""
            st.markdown(f"**{ts}**{people_str}  \n{text}")
            st.divider()
    else:
        st.info("No memories in the last 24 hours.")

    st.subheader("Registered Family Members")
    persons = api("GET", f"/face/{user_id}/persons")
    if persons:
        cols = st.columns(min(len(persons), 4))
        for i, person in enumerate(persons):
            with cols[i % 4]:
                name = person.get("name", "Unknown")
                rel = person.get("relationship", "")
                last_seen = person.get("last_seen")
                confirmed = person.get("confirmed", False)
                count = person.get("interaction_count", 0)
                badge = "✅" if confirmed else "⚠️ Unconfirmed"
                st.markdown(
                    f"**{name}** {badge}  \n"
                    f"_{rel}_  \n"
                    f"Seen: {time_ago(last_seen) if last_seen else 'Never'}  \n"
                    f"Interactions: {count}"
                )
    else:
        st.info("No family members registered yet.")

# ===========================================================================
# TAB 2 — MEMORIES
# ===========================================================================

with tab_memories:
    st.header("Episodic Memory Search")

    search_col, filter_col = st.columns([3, 1])
    with search_col:
        query = st.text_input("Search memories", placeholder="Who came yesterday?")
    with filter_col:
        k = st.number_input("Results", min_value=1, max_value=20, value=5)

    if query:
        results = api(
            "POST",
            "/memory/query",
            json={"query": query, "user_id": user_id, "k": int(k)},
        )
        if results:
            for chunk in results:
                ts = chunk.get("timestamp", "")
                text = chunk.get("text", "")
                people = chunk.get("people", [])
                tags = chunk.get("tags", [])
                with st.expander(f"{time_ago(ts)} — {text[:80]}..."):
                    st.write(text)
                    if people:
                        st.write("👤 People:", ", ".join(people))
                    if tags:
                        st.write("🏷️ Tags:", ", ".join(tags))
                    if chunk.get("location"):
                        loc = chunk["location"]
                        st.write(f"📍 Location: {loc.get('lat'):.4f}, {loc.get('lon'):.4f}")
        else:
            st.info("No memories found.")

    st.divider()
    st.subheader("Memory Timeline")

    timeline_hours = st.slider("Show last N hours", 1, 168, 48)
    timeline_data = api(
        "GET", f"/memory/{user_id}/recent", params={"hours": timeline_hours, "limit": 50}
    )
    if timeline_data:
        df = pd.DataFrame(
            [
                {
                    "Time": chunk["timestamp"],
                    "Text": chunk["text"][:60] + "..." if len(chunk["text"]) > 60 else chunk["text"],
                    "People": ", ".join(chunk.get("people", [])),
                    "Type": chunk.get("memory_type", "episodic"),
                }
                for chunk in timeline_data
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

        if len(df) > 1:
            df["Time"] = pd.to_datetime(df["Time"])
            df["Hour"] = df["Time"].dt.hour
            hourly = df.groupby("Hour").size().reset_index(name="Count")
            fig = px.bar(
                hourly, x="Hour", y="Count",
                title="Memory Activity by Hour of Day",
                color_discrete_sequence=["#FF8C00"],
            )
            st.plotly_chart(fig, use_container_width=True)

# ===========================================================================
# TAB 3 — ALERTS
# ===========================================================================

with tab_anomalies:
    st.header("Active Alerts")

    active = api("GET", f"/anomaly/{user_id}/active")
    if active:
        for event in active:
            severity = event.get("severity", "low")
            event_type = event.get("event_type", "unknown")
            description = event.get("description", "")
            ts = event.get("timestamp", "")
            anomaly_id = event.get("id", event.get("metadata", {}).get("id", ""))

            badge = severity_badge(severity)
            with st.container(border=True):
                col_info, col_action = st.columns([4, 1])
                with col_info:
                    st.markdown(f"### {badge} {event_type.replace('_', ' ').title()}")
                    st.write(description)
                    st.caption(f"Detected: {time_ago(ts)}")
                with col_action:
                    if anomaly_id and st.button("Resolve", key=f"resolve_{anomaly_id}"):
                        result = api("POST", f"/anomaly/{anomaly_id}/resolve")
                        if result:
                            st.success("Resolved")
                            st.rerun()
    else:
        st.success("✅ No active alerts")

    st.divider()
    st.subheader("Start Background Monitoring")

    webhook_url = st.text_input("Caregiver webhook URL (optional)", placeholder="https://...")
    interval = st.slider("Check interval (seconds)", 60, 900, 300)
    if st.button("Start Monitoring"):
        result = api(
            "POST",
            f"/anomaly/{user_id}/start-monitoring",
            json={"webhook_url": webhook_url or None, "interval": interval},
        )
        if result:
            st.success(f"Monitoring started — checking every {interval}s")

# ===========================================================================
# TAB 4 — ROUTINE
# ===========================================================================

with tab_routine:
    st.header("Daily Routine")

    routine = api("GET", f"/routine/{user_id}")
    if routine and "error" not in str(routine):
        col_r1, col_r2 = st.columns(2)

        with col_r1:
            st.subheader("Learned Schedule")
            meal_times = routine.get("meal_times", {})
            for meal, hours in meal_times.items():
                if isinstance(hours, list) and len(hours) == 2:
                    st.write(f"**{meal.title()}**: {hours[0]:02d}:00 – {hours[1]:02d}:00")
            wake = routine.get("wake_time", [])
            sleep = routine.get("sleep_time", [])
            if wake:
                st.write(f"**Wake**: {wake[0]:02d}:00")
            if sleep:
                st.write(f"**Sleep**: {sleep[0]:02d}:00")
            obs_days = routine.get("observation_days", 0)
            st.caption(f"Based on {obs_days} observation days")

        with col_r2:
            st.subheader("Medications")
            meds = routine.get("medication_schedule", [])
            if meds:
                for med in meds:
                    name = med.get("name", "")
                    times = med.get("times", [])
                    st.write(f"💊 **{name}**: {', '.join(times)}")
            else:
                st.info("No medications configured.")

        st.subheader("Log an Event")
        event_col1, event_col2 = st.columns(2)
        with event_col1:
            event_type = st.selectbox(
                "Event type",
                ["meal", "sleep", "wake", "activity", "medication"],
            )
        with event_col2:
            event_time = st.time_input("Time", value=datetime.now().time())

        metadata: dict[str, Any] = {}
        if event_type == "medication":
            metadata["name"] = st.text_input("Medication name")
            metadata["taken"] = st.checkbox("Taken (uncheck = skipped)", value=True)
        elif event_type == "meal":
            metadata["meal"] = st.selectbox("Meal", ["breakfast", "lunch", "dinner", "snack"])
        elif event_type == "activity":
            metadata["name"] = st.text_input("Activity name")

        if st.button("Log Event"):
            event_dt = datetime.combine(datetime.today(), event_time, tzinfo=timezone.utc)
            result = api(
                "POST",
                f"/routine/{user_id}/event",
                json={
                    "type": event_type,
                    "timestamp": event_dt.isoformat(),
                    "metadata": metadata,
                },
            )
            if result:
                st.success("Event logged")
    else:
        st.info("No routine data yet. Events will be learned automatically from usage.")

# ===========================================================================
# TAB 5 — EVALUATION
# ===========================================================================

with tab_eval:
    st.header("System Evaluation")
    st.info(
        "Runs Sahayak against 50 synthetic dementia scenarios. "
        "Takes 10–30 minutes depending on LLM latency."
    )

    scenarios_meta = api("GET", "/eval/scenarios")
    if scenarios_meta:
        meta_df = pd.DataFrame(scenarios_meta)
        category_counts = meta_df["category"].value_counts()
        diff_counts = meta_df["difficulty"].value_counts()

        col_e1, col_e2 = st.columns(2)
        with col_e1:
            fig_cat = px.pie(
                names=category_counts.index,
                values=category_counts.values,
                title="Scenarios by Category",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            st.plotly_chart(fig_cat, use_container_width=True)
        with col_e2:
            fig_diff = px.pie(
                names=diff_counts.index,
                values=diff_counts.values,
                title="Scenarios by Difficulty",
                color_discrete_sequence=["#4CAF50", "#FF9800", "#F44336"],
            )
            st.plotly_chart(fig_diff, use_container_width=True)

    st.subheader("Run Evaluation")
    eval_user_id = st.text_input("Eval user ID", value="eval_user")
    run_subset = st.toggle("Run subset only", value=False)
    subset_ids: list[str] = []
    if run_subset and scenarios_meta:
        available_ids = [s["id"] for s in scenarios_meta]
        subset_ids = st.multiselect("Select scenario IDs", available_ids, default=available_ids[:10])

    if st.button("🚀 Start Evaluation", type="primary"):
        payload: dict[str, Any] = {"user_id": eval_user_id}
        if run_subset and subset_ids:
            payload["scenario_ids"] = subset_ids
        job = api("POST", "/eval/run", json=payload)
        if job:
            job_id = job.get("job_id", "")
            st.session_state["eval_job_id"] = job_id
            st.success(f"Job started: `{job_id}`")

    if "eval_job_id" in st.session_state:
        job_id = st.session_state["eval_job_id"]
        st.subheader(f"Job: `{job_id}`")
        results = api("GET", f"/eval/results/{job_id}")
        if results and results.get("status") != "running":
            st.metric("Mean Score", f"{results.get('mean_score', 0):.2%}")
            st.metric("Pass Rate", f"{results.get('pass_rate', 0):.2%}")
            st.metric("Mean Latency", f"{results.get('mean_latency_ms', 0):.0f}ms")

            if "by_category" in results:
                cat_df = pd.DataFrame(
                    [{"Category": k, "Score": v} for k, v in results["by_category"].items()]
                )
                fig_scores = px.bar(
                    cat_df, x="Category", y="Score",
                    title="Score by Category",
                    color="Score",
                    color_continuous_scale="RdYlGn",
                    range_color=[0, 1],
                )
                st.plotly_chart(fig_scores, use_container_width=True)

            if st.button("📄 Download Report"):
                report = api("GET", f"/eval/report/{job_id}")
                if report:
                    st.download_button(
                        "Download Markdown",
                        data=str(report),
                        file_name=f"sahayak_eval_{job_id[:8]}.md",
                        mime="text/markdown",
                    )
        elif results and results.get("status") == "running":
            st.info("Evaluation running... refresh in a minute.")
            if st.button("Check Status"):
                st.rerun()

# ===========================================================================
# TAB 6 — FEDERATED LEARNING
# ===========================================================================

with tab_fl:
    st.header("Federated Learning")
    st.markdown(
        "Sahayak uses federated learning to personalize intervention timing "
        "across consenting devices without sharing raw episodic data."
    )

    fl_status = api("GET", "/federation-server/status")
    if fl_status:
        if fl_status.get("running"):
            st.success(f"FL server running at `{fl_status.get('address')}`")
        else:
            st.warning("FL server not running")

    col_fl1, col_fl2 = st.columns(2)
    with col_fl1:
        fl_address = st.text_input("FL server address", value="localhost:9090")
        fl_rounds = st.number_input("Training rounds", min_value=1, max_value=20, value=3)
        if st.button("Start FL Server"):
            result = api(
                "POST",
                "/federation-server/start",
                json={"num_rounds": int(fl_rounds), "address": fl_address},
            )
            if result:
                st.success(f"FL server started at {fl_address}")
                st.rerun()

    with col_fl2:
        st.subheader("Log Caregiver Feedback")
        st.caption("Thumbs-up/down trains the intervention timing model on-device.")
        interaction_id = st.text_input("Interaction ID")
        thumbs_up = st.toggle("👍 Helpful", value=True)
        if st.button("Submit Feedback") and interaction_id:
            result = api(
                "POST",
                "/federation/feedback",
                json={
                    "user_id": user_id,
                    "interaction_id": interaction_id,
                    "thumbs_up": thumbs_up,
                    "features": {},
                },
            )
            if result:
                st.success("Feedback logged")

    st.subheader("Trigger FL Round")
    if st.button("Run FL Round Now"):
        result = api("POST", "/federation/round")
        if result:
            st.success("FL round completed")
            st.json(result)
