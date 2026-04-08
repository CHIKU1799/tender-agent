#!/usr/bin/env python3
"""
Tender Agent — Analytics Dashboard
───────────────────────────────────────────────────────────────────────
Run:  streamlit run dashboard.py
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Config ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("output")
DB_PATH = OUTPUT_DIR / "tenders.db"
DAILY_DIR = OUTPUT_DIR / "daily"

st.set_page_config(
    page_title="Tender Agent — Analytics",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    """Load tenders from SQLite or fallback to CSV/JSON files."""
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        try:
            df = pd.read_sql_query("SELECT * FROM tenders", conn)
            conn.close()
            if not df.empty:
                return df
        except Exception:
            conn.close()

    # Fallback: load all CSV files
    frames = []
    for csv_file in OUTPUT_DIR.glob("*_tenders.csv"):
        if csv_file.name == "all_tenders.csv":
            continue
        try:
            df = pd.read_csv(csv_file, encoding="utf-8-sig")
            frames.append(df)
        except Exception:
            pass

    if frames:
        return pd.concat(frames, ignore_index=True)

    # Fallback: JSON files
    for json_file in OUTPUT_DIR.glob("*_tenders.json"):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            if data:
                frames.append(pd.DataFrame(data))
        except Exception:
            pass

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_daily_history() -> pd.DataFrame:
    """Load daily snapshot counts for trend analysis."""
    if not DAILY_DIR.exists():
        return pd.DataFrame()

    records = []
    for date_dir in sorted(DAILY_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        for json_file in date_dir.glob("*_tenders.json"):
            portal_id = json_file.stem.replace("_tenders", "")
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
                records.append({
                    "date": date_str,
                    "portal_id": portal_id,
                    "count": len(data),
                })
            except Exception:
                pass

    return pd.DataFrame(records) if records else pd.DataFrame()


# ── Helper functions ─────────────────────────────────────────────────────────

def parse_date_col(df: pd.DataFrame, col: str) -> pd.Series:
    """Try to parse a date column with multiple formats."""
    if col not in df.columns:
        return pd.Series(dtype="datetime64[ns]", index=df.index)
    return pd.to_datetime(df[col], format="mixed", errors="coerce", dayfirst=True)


def get_portal_display_name(portal_id: str) -> str:
    names = {
        "cppp": "CPPP (Central)",
        "gem": "GeM",
        "defproc": "Defence",
        "ireps": "Railways (IREPS)",
        "etenders": "NIC eTenders",
        "ntpc": "NTPC",
        "coalindia": "Coal India",
        "karnataka": "Karnataka eProcure",
        "karnataka_kppp": "Karnataka KPPP",
        "maharashtra": "Maharashtra",
        "up": "Uttar Pradesh",
        "tamilnadu": "Tamil Nadu",
        "gujarat": "Gujarat (nprocure)",
        "rajasthan": "Rajasthan",
        "delhi": "Delhi",
        "mp": "Madhya Pradesh",
        "uttarakhand": "Uttarakhand",
        "tenderdetail": "TenderDetail.com",
        "tendertiger": "TenderTiger.com",
        "tenderkart": "TenderKart.in",
        "tender247": "Tender247.com",
        "gujarattenders": "GujaratTenders.in",
        "palladium": "Palladium",
        "bhel": "BHEL",
        "hal": "HAL",
        "ongc": "ONGC",
    }
    return names.get(portal_id, portal_id.replace("_", " ").title())


PORTAL_TYPE_MAP = {
    "cppp": "Central Govt", "gem": "Central Govt", "defproc": "Central Govt",
    "ireps": "Central Govt", "etenders": "Central Govt",
    "ntpc": "PSU", "coalindia": "PSU", "bhel": "PSU", "hal": "PSU", "ongc": "PSU",
    "karnataka": "State Govt", "karnataka_kppp": "State Govt",
    "maharashtra": "State Govt", "up": "State Govt", "tamilnadu": "State Govt",
    "gujarat": "State Govt", "rajasthan": "State Govt", "delhi": "State Govt",
    "mp": "State Govt", "uttarakhand": "State Govt",
    "tenderdetail": "Aggregator", "tendertiger": "Aggregator",
    "tenderkart": "Aggregator", "tender247": "Aggregator",
    "gujarattenders": "Aggregator", "palladium": "Aggregator",
}


# ── Dashboard ────────────────────────────────────────────────────────────────

def main():
    st.title("🏛️ Tender Agent — Analytics Dashboard")
    st.caption("Multi-portal Indian Government Tender Intelligence")

    df = load_data()

    if df.empty:
        st.warning(
            "No tender data found. Run the scraper first:\n\n"
            "```bash\npython main.py\n# or\npython scrape_karnataka.py\n```"
        )
        return

    # Clean data
    df["portal_display"] = df["portal_id"].apply(get_portal_display_name)
    df["portal_type"] = df["portal_id"].map(PORTAL_TYPE_MAP).fillna("Other")
    df["scraped_date"] = parse_date_col(df, "scraped_at").dt.date
    df["closing_dt"] = parse_date_col(df, "closing_date")
    df["published_dt"] = parse_date_col(df, "published_date")
    df["has_details"] = df.get("detail_scraped", pd.Series(dtype=str)).astype(str).str.lower().isin(["true", "1", "yes"])
    df["has_value"] = df.get("tender_value_inr", pd.Series(dtype=str)).astype(str).str.strip().ne("")

    # ── Sidebar filters ──────────────────────────────────────────────────────
    with st.sidebar:
        st.header("🔧 Filters")

        portal_types = sorted(df["portal_type"].unique())
        selected_types = st.multiselect("Portal Type", portal_types, default=portal_types)

        portals = sorted(df[df["portal_type"].isin(selected_types)]["portal_display"].unique())
        selected_portals = st.multiselect("Portals", portals, default=portals)

        if "industry_category" in df.columns:
            categories = sorted(df["industry_category"].dropna().unique())
            if categories:
                selected_cats = st.multiselect("Industry Category", categories, default=categories)
                df = df[df["industry_category"].isin(selected_cats) | df["industry_category"].isna()]

        st.divider()
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Apply filters
    mask = df["portal_display"].isin(selected_portals) & df["portal_type"].isin(selected_types)
    fdf = df[mask].copy()

    # ── KPI Row ──────────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    now = pd.Timestamp.now()
    closing_soon = fdf[(fdf["closing_dt"] >= now) & (fdf["closing_dt"] <= now + timedelta(days=7))]

    col1.metric("Total Tenders", f"{len(fdf):,}")
    col2.metric("Portals Scraped", fdf["portal_id"].nunique())
    col3.metric("With Detail Data", f"{fdf['has_details'].sum():,}")
    col4.metric("Closing in 7 Days", f"{len(closing_soon):,}", delta_color="inverse")
    if "industry_category" in fdf.columns:
        col5.metric("Categories", fdf["industry_category"].dropna().nunique())
    else:
        col5.metric("Organisations", f"{fdf['organisation'].dropna().nunique():,}" if "organisation" in fdf.columns else "0")

    st.divider()

    # ── Charts Row 1: Tenders by Platform + Detail Coverage ──────────────────
    chart1, chart2 = st.columns(2)

    with chart1:
        st.subheader("📊 Tenders by Platform")
        portal_counts = (
            fdf.groupby(["portal_display", "portal_type"])
            .size().reset_index(name="count")
            .sort_values("count", ascending=True)
        )
        fig = px.bar(
            portal_counts, x="count", y="portal_display", color="portal_type",
            orientation="h",
            color_discrete_map={
                "Central Govt": "#1f77b4", "State Govt": "#2ca02c",
                "PSU": "#ff7f0e", "Aggregator": "#9467bd", "Other": "#8c564b",
            },
            labels={"count": "Tenders", "portal_display": "", "portal_type": "Type"},
        )
        fig.update_layout(height=max(350, len(portal_counts) * 30), margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

    with chart2:
        st.subheader("🏷️ Detail Coverage by Platform")
        detail_stats = (
            fdf.groupby("portal_display")
            .agg(total=("portal_id", "size"), with_details=("has_details", "sum"))
            .reset_index()
        )
        detail_stats["without_details"] = detail_stats["total"] - detail_stats["with_details"]
        detail_stats = detail_stats.sort_values("total", ascending=True)

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            y=detail_stats["portal_display"], x=detail_stats["with_details"],
            name="With Details", orientation="h", marker_color="#2ca02c",
        ))
        fig2.add_trace(go.Bar(
            y=detail_stats["portal_display"], x=detail_stats["without_details"],
            name="Listing Only", orientation="h", marker_color="#d62728", opacity=0.6,
        ))
        fig2.update_layout(
            barmode="stack", height=max(350, len(detail_stats) * 30),
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── Charts Row 2: Pie + Category/Org ─────────────────────────────────────
    chart3, chart4 = st.columns(2)

    with chart3:
        st.subheader("📈 Portal Type Distribution")
        type_counts = fdf["portal_type"].value_counts().reset_index()
        type_counts.columns = ["Portal Type", "Count"]
        fig3 = px.pie(
            type_counts, values="Count", names="Portal Type",
            color="Portal Type",
            color_discrete_map={
                "Central Govt": "#1f77b4", "State Govt": "#2ca02c",
                "PSU": "#ff7f0e", "Aggregator": "#9467bd",
            },
            hole=0.4,
        )
        fig3.update_layout(height=350, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig3, use_container_width=True)

    with chart4:
        if "industry_category" in fdf.columns and fdf["industry_category"].notna().any():
            st.subheader("🏭 Industry Categories")
            cat_counts = fdf["industry_category"].dropna().value_counts().head(15).reset_index()
            cat_counts.columns = ["Category", "Count"]
            fig4 = px.bar(
                cat_counts, x="Count", y="Category", orientation="h",
                color="Count", color_continuous_scale="Viridis",
            )
            fig4.update_layout(height=350, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
            st.plotly_chart(fig4, use_container_width=True)
        elif "organisation" in fdf.columns:
            st.subheader("🏢 Top Organisations")
            org_counts = (
                fdf["organisation"].dropna().str.strip()
                .loc[lambda s: s != ""].value_counts().head(15).reset_index()
            )
            org_counts.columns = ["Organisation", "Count"]
            fig4 = px.bar(
                org_counts, x="Count", y="Organisation", orientation="h",
                color="Count", color_continuous_scale="Blues",
            )
            fig4.update_layout(height=350, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
            st.plotly_chart(fig4, use_container_width=True)

    # ── Timeline ─────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📅 Scraping Timeline")

    if fdf["scraped_date"].notna().any():
        timeline = (
            fdf[fdf["scraped_date"].notna()]
            .groupby(["scraped_date", "portal_type"])
            .size().reset_index(name="count")
        )
        timeline["scraped_date"] = pd.to_datetime(timeline["scraped_date"])
        fig5 = px.area(
            timeline, x="scraped_date", y="count", color="portal_type",
            labels={"scraped_date": "Date", "count": "Tenders Scraped", "portal_type": "Type"},
        )
        fig5.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig5, use_container_width=True)

    # Daily history
    daily_df = load_daily_history()
    if not daily_df.empty:
        st.subheader("📊 Daily Scrape History")
        daily_df["date"] = pd.to_datetime(daily_df["date"])
        daily_agg = daily_df.groupby("date")["count"].sum().reset_index()
        fig_daily = px.bar(daily_agg, x="date", y="count",
                           labels={"date": "Date", "count": "Tenders"},
                           color_discrete_sequence=["#2ca02c"])
        fig_daily.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_daily, use_container_width=True)

    # ── Closing Soon Table ───────────────────────────────────────────────────
    st.divider()
    st.subheader("⏰ Tenders Closing Soon (Next 7 Days)")

    if not closing_soon.empty:
        display_cols = ["portal_display", "ref_number", "title", "organisation", "closing_date", "tender_value_inr"]
        available_cols = [c for c in display_cols if c in closing_soon.columns]
        st.dataframe(
            closing_soon[available_cols].sort_values("closing_dt").head(50)
            .rename(columns={
                "portal_display": "Portal", "ref_number": "Ref No.",
                "title": "Title", "organisation": "Organisation",
                "closing_date": "Closing Date", "tender_value_inr": "Value (INR)",
            }),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No tenders closing in the next 7 days found.")

    # ── Full Data Table ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("📋 All Tenders Data")

    search = st.text_input("🔍 Search tenders (title, org, ref number):", "")
    if search:
        sl = search.lower()
        search_mask = (
            fdf["title"].fillna("").str.lower().str.contains(sl, na=False) |
            fdf.get("organisation", pd.Series(dtype=str)).fillna("").str.lower().str.contains(sl, na=False) |
            fdf.get("ref_number", pd.Series(dtype=str)).fillna("").str.lower().str.contains(sl, na=False)
        )
        display_df = fdf[search_mask]
    else:
        display_df = fdf

    all_cols = list(display_df.columns)
    default_cols = [c for c in [
        "portal_display", "ref_number", "title", "organisation",
        "published_date", "closing_date", "tender_value_inr",
        "status", "industry_category", "location",
    ] if c in all_cols]

    selected_cols = st.multiselect("Columns to display:", all_cols, default=default_cols or all_cols[:8])
    if selected_cols:
        st.dataframe(display_df[selected_cols].head(500), use_container_width=True, hide_index=True, height=400)
        st.caption(f"Showing {min(500, len(display_df))} of {len(display_df):,} tenders")

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    ce1, ce2 = st.columns(2)
    with ce1:
        st.download_button(
            "📥 Download Filtered CSV",
            fdf.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"tenders_export_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
    with ce2:
        st.download_button(
            "📥 Download Filtered JSON",
            fdf.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8"),
            file_name=f"tenders_export_{datetime.now().strftime('%Y%m%d')}.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()
