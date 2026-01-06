import streamlit as st
import sqlite3
import pandas as pd
import time
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- CONFIGURATION ---
st.set_page_config(
    page_title="RLD v3 Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for "Pro" Look
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .metric-card {
        background-color: #1a1c24;
        border-radius: 10px;
        padding: 20px;
        border: 1px solid #2d2f3a;
    }
    h1, h2, h3 { color: #e6e6e6; font-family: 'Inter', sans-serif; }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
st.sidebar.title("⚡ RLD v3 Protocol")
st.sidebar.markdown("### Data Settings")

exclude_history = st.sidebar.toggle("Exclude Daily History", value=False)

# Date Filter
st.sidebar.subheader("View Range")
range_options = ["Last 24 Hours", "Last 7 Days", "Last 30 Days", "Year to Date", "Max"]
selected_range = st.sidebar.selectbox("Quick Select", range_options, index=1)

# TWAR Settings
st.sidebar.subheader("Oracle Config")
show_twar = st.sidebar.checkbox("Show TWAR", value=True)
twar_minutes = st.sidebar.number_input("Window (Minutes)", min_value=1, value=60, step=10)

# --- DATA ENGINE ---
@st.cache_data(ttl=12)  # Cache data for 12 seconds (1 block)
def load_data(limit=500000):
    """
    Loads raw data. Caching prevents re-reading DB on every UI click.
    """
    conn = sqlite3.connect('file:aave_rates.db?mode=ro', uri=True)
    query = f"SELECT * FROM rates ORDER BY timestamp DESC LIMIT {limit}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
        df = df.sort_values('timestamp')
        df = df.set_index('timestamp')
    return df

def process_data(df, time_range):
    """
    1. Filters by Date
    2. Calculates TWAR (High Precision)
    3. Downsamples for Charting (High Performance)
    """
    if df.empty: return df, df

    # 1. Filter Range
    end_date = df.index.max()
    if time_range == "Last 24 Hours":
        start_date = end_date - timedelta(hours=24)
    elif time_range == "Last 7 Days":
        start_date = end_date - timedelta(days=7)
    elif time_range == "Last 30 Days":
        start_date = end_date - timedelta(days=30)
    elif time_range == "Year to Date":
        start_date = pd.Timestamp(f"{end_date.year}-01-01")
    else:
        start_date = df.index.min()

    mask = df.index >= start_date
    filtered_df = df.loc[mask].copy()

    # 2. TWAR Calculation (On High-Res Data)
    if show_twar and not filtered_df.empty:
        # Resample to minute grid for accuracy, then roll
        minute_grid = filtered_df['apy'].resample('1min').mean().ffill()
        twar_series = minute_grid.rolling(window=twar_minutes, min_periods=1).mean()
        
        # Merge back
        filtered_df = pd.merge_asof(
            filtered_df, 
            twar_series.rename('TWAR'), 
            left_index=True, 
            right_index=True, 
            direction='backward'
        )

    # 3. Downsampling for Display
    # Browsers choke on >5k points. We aggregate for visual only.
    total_points = len(filtered_df)
    target_points = 2000 # Max points to draw
    
    if total_points > target_points:
        resample_rule = f"{int(total_points / target_points * 12)}S" # Approx rule
        # If rule is too small, fallback to minute/hour
        if time_range == "Year to Date" or time_range == "Max":
            resample_rule = "4H"
        elif time_range == "Last 30 Days":
            resample_rule = "1H"
        elif time_range == "Last 7 Days":
            resample_rule = "15min"
        
        display_df = filtered_df.resample(resample_rule).mean().dropna()
    else:
        display_df = filtered_df

    return filtered_df, display_df

# --- CHART ENGINE ---
def create_pro_chart(df):
    fig = go.Figure()

    # 1. Variable Borrow Rate (Area with Gradient)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['apy'],
        mode='lines',
        name='Variable APY',
        line=dict(width=2, color='#00f2ff'), # Cyan Neon
        fill='tozeroy',
        fillcolor='rgba(0, 242, 255, 0.1)' # Transparent fill
    ))

    # 2. TWAR (Solid Line)
    if show_twar and 'TWAR' in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df['TWAR'],
            mode='lines',
            name=f'{twar_minutes}m TWAR',
            line=dict(width=3, color='#ff0055') # Pink Neon
        ))

    # Layout Styling
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=550,
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(
            showgrid=True, 
            gridcolor='#2d2f3a', 
            gridwidth=0.5
        ),
        yaxis=dict(
            title="APY (%)", 
            showgrid=True, 
            gridcolor='#2d2f3a', 
            gridwidth=0.5,
            zeroline=False
        ),
        hovermode="x unified",
        legend=dict(
            yanchor="top", y=0.99, xanchor="left", x=0.01,
            bgcolor="rgba(0,0,0,0.5)"
        )
    )
    return fig

# --- MAIN LOOP ---
placeholder = st.empty()

while True:
    try:
        raw_df = load_data()
        
        if exclude_history:
            raw_df = raw_df[raw_df['block_number'] > 0]

        with placeholder.container():
            if raw_df.empty:
                st.warning("waiting for data pipeline...")
            else:
                # Process
                calc_df, plot_df = process_data(raw_df, selected_range)
                
                if not calc_df.empty:
                    latest = calc_df.iloc[-1]
                    
                    # --- METRICS ROW ---
                    c1, c2, c3, c4 = st.columns(4)
                    
                    with c1:
                        st.metric("Live Borrow Rate", f"{latest['apy']:.2f}%", delta_color="normal")
                    
                    with c2:
                        if 'TWAR' in latest:
                            delta = latest['apy'] - latest['TWAR']
                            st.metric(f"Oracle ({twar_minutes}m)", f"{latest['TWAR']:.2f}%", 
                                      delta=f"{delta:.2f}% vs Spot", delta_color="inverse")
                        else:
                            st.metric("Oracle", "Loading...")

                    with c3:
                        # Volatility (Std Dev of last 24h)
                        vol_window = calc_df.tail(7200) # approx 24h
                        vol = vol_window['apy'].std()
                        st.metric("24h Volatility", f"±{vol:.2f}%")

                    with c4:
                        last_ts = latest.name
                        diff = int((datetime.utcnow() - last_ts).total_seconds())
                        color = "normal" if diff < 60 else "off"
                        st.metric("Heartbeat", f"{diff}s ago", delta_color=color)

                    # --- CHART ---
                    st.plotly_chart(create_pro_chart(plot_df), use_container_width=True)

                    # --- DEBUG INFO ---
                    with st.expander(f"System Stats"):
                        st.caption(f"Raw Datapoints: {len(raw_df):,} | Rendered Points: {len(plot_df):,}")
                        st.caption(f"DB Path: aave_rates.db | RPC: Active")

    except Exception as e:
        st.error(f"Dashboard Error: {e}")
    
    time.sleep(2) # UI Refresh Rate