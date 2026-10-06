"""
IVR Campaign Dashboard — local Flask backend
=============================================
Runs entirely on your machine. Point it at any DCS_<district>*.csv file
(same schema as the district reports you've been building) and it will:
  - clean the data the same way your Colab script does
  - let you pick a date range (a single day, or a whole week) via the UI
  - compute KPIs, charts, and a rule-based "why is this happening" insight
    engine (timeout diagnosis, no-DTMF diagnosis, busy-hour patterns, etc.)

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import os
import uuid
import functools
from datetime import datetime

import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, render_template

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)

STATUS_ORDER = ["Connected", "Timeout", "User Not Responding", "Busy", "Line Drop"]
STATUS_COLORS = {
    "Connected": "#FF9F45",
    "Timeout": "#5B8DEF",
    "User Not Responding": "#B8C2CC",
    "Busy": "#FFD666",
    "Line Drop": "#FF5C5C",
}

EMPTY_COLS = [
    "Unnamed: 6", "B PARTY NO", "B PARTY DIAL STATUS", "B PARTY CALL START DATE",
    "B PARTY CALL START TIME", "B PARTY CALL END DATE", "B PARTY CALL END TIME",
    "B PARTY DIAL START DATE", "B PARTY DIAL START TIME", "B PARTY DIAL END DATE",
    "B PARTY DIAL END TIME", "B PARTY CALL DURATION(In Seconds)", "SEND DTMF",
    "CAMPAIGN NAME",
]

TALK_BUCKETS = [0, 10, 20, 30, 60, 120, np.inf]
TALK_BUCKET_LABELS = ["0-10s", "10-20s", "20-30s", "30-60s", "60-120s", "120s+"]

DURATION_HIST_BINS = list(range(0, 181, 10)) + [np.inf]
DURATION_HIST_LABELS = [f"{DURATION_HIST_BINS[i]}-{DURATION_HIST_BINS[i+1]}s"
                         if DURATION_HIST_BINS[i + 1] != np.inf else "180s+"
                         for i in range(len(DURATION_HIST_BINS) - 1)]


# ------------------------------------------------------------------
# Loading + cleaning (mirrors the Colab script), cached by file mtime
# ------------------------------------------------------------------
_cache = {}


def _combine_date_time(date_col, time_col):
    if pd.api.types.is_datetime64_any_dtype(date_col):
        date_col = date_col.dt.strftime("%d-%m-%Y")
    if pd.api.types.is_datetime64_any_dtype(time_col):
        time_col = time_col.dt.strftime("%H:%M:%S")
    combined = date_col.astype(str) + " " + time_col.astype(str)
    return pd.to_datetime(combined, format="%d-%m-%Y %H:%M:%S", errors="coerce")


def load_and_clean(path):
    mtime = os.path.getmtime(path)
    key = (path, mtime)
    if key in _cache:
        return _cache[key]

    df = pd.read_csv(path)
    empty_cols = [c for c in EMPTY_COLS if c in df.columns]
    df = df.drop(columns=empty_cols).drop_duplicates()

    required = ["A PARTY DIAL STATUS", "A PARTY DIAL START DATE", "A PARTY DIAL START TIME",
                "A PARTY DIAL END DATE", "A PARTY DIAL END TIME", "A PARTY CALL START TIME",
                "TOTAL DURATION(In Seconds)"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing expected columns: {missing}")

    df["Dial Start"] = _combine_date_time(df["A PARTY DIAL START DATE"], df["A PARTY DIAL START TIME"])
    df["Dial End"] = _combine_date_time(df["A PARTY DIAL END DATE"], df["A PARTY DIAL END TIME"])
    df["Dial Duration (sec)"] = (df["Dial End"] - df["Dial Start"]).dt.total_seconds()
    df["Hour"] = df["Dial Start"].dt.hour
    df["Date"] = df["Dial Start"].dt.date
    df["Weekday"] = df["Dial Start"].dt.day_name()

    call_start = pd.to_datetime(df["A PARTY CALL START TIME"], format="%H:%M:%S", errors="coerce")
    df["Call Hour"] = call_start.dt.hour

    if "DTMF" in df.columns:
        df["DTMF_num"] = pd.to_numeric(df["DTMF"], errors="coerce")
    else:
        df["DTMF_num"] = np.nan

    _cache[key] = df
    # keep cache from growing unbounded across many uploaded files
    if len(_cache) > 20:
        _cache.pop(next(iter(_cache)))
    return df


def resolve_csv_path(csv_id):
    for folder in (UPLOAD_DIR, DATA_DIR):
        p = os.path.join(folder, csv_id)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(csv_id)


def list_available_csvs():
    out = []
    for folder in (DATA_DIR, UPLOAD_DIR):
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".csv"):
                continue
            path = os.path.join(folder, fn)
            entry = {"id": fn, "filename": fn, "rows": None, "date_min": None, "date_max": None, "error": None}
            try:
                df = load_and_clean(path)
                entry["rows"] = int(len(df))
                valid_dates = df["Date"].dropna()
                if len(valid_dates):
                    entry["date_min"] = str(valid_dates.min())
                    entry["date_max"] = str(valid_dates.max())
            except Exception as e:  # noqa: BLE001
                entry["error"] = str(e)
            out.append(entry)
    return out


def filter_by_date(df, start_date, end_date):
    if not start_date and not end_date:
        return df
    d = df["Date"]
    mask = pd.Series(True, index=df.index)
    if start_date:
        mask &= d >= pd.to_datetime(start_date).date()
    if end_date:
        mask &= d <= pd.to_datetime(end_date).date()
    return df[mask]


# ------------------------------------------------------------------
# KPI / chart computation
# ------------------------------------------------------------------
def compute_dashboard(df):
    total_attempts = len(df)
    connected = df[df["A PARTY DIAL STATUS"] == "Connected"].copy()
    conn_dur = connected.dropna(subset=["TOTAL DURATION(In Seconds)"]).copy()

    status_counts = df["A PARTY DIAL STATUS"].value_counts()
    status_breakdown = []
    for s in STATUS_ORDER:
        if s in status_counts.index:
            c = int(status_counts[s])
            status_breakdown.append({"status": s, "count": c,
                                      "pct": round(c / total_attempts * 100, 2) if total_attempts else 0,
                                      "color": STATUS_COLORS[s]})
    # include any status not in our known order (defensive)
    for s in status_counts.index:
        if s not in STATUS_ORDER:
            c = int(status_counts[s])
            status_breakdown.append({"status": s, "count": c,
                                      "pct": round(c / total_attempts * 100, 2) if total_attempts else 0,
                                      "color": "#888888"})

    connect_rate = round(len(connected) / total_attempts * 100, 2) if total_attempts else 0
    dtmf_pressed = int(connected["DTMF_num"].notna().sum()) if "DTMF_num" in connected else 0
    dtmf_rate = round(dtmf_pressed / len(connected) * 100, 2) if len(connected) else 0
    avg_dur = float(conn_dur["TOTAL DURATION(In Seconds)"].mean()) if len(conn_dur) else 0
    median_dur = float(conn_dur["TOTAL DURATION(In Seconds)"].median()) if len(conn_dur) else 0

    # "positive response" = DTMF pressed with a non-zero / non-negative keypad value
    if dtmf_pressed:
        positive = connected[connected["DTMF_num"].notna()]
        positive_count = int((positive["DTMF_num"] >= 0).sum())
    else:
        positive_count = 0
    positive_rate = round(positive_count / dtmf_pressed * 100, 2) if dtmf_pressed else 0

    kpis = {
        "total_attempts": total_attempts,
        "connected": int(len(connected)),
        "connect_rate": connect_rate,
        "failed_total": int(total_attempts - len(connected)),
        "failure_rate": round(100 - connect_rate, 2) if total_attempts else 0,
        "dtmf_pressed": dtmf_pressed,
        "dtmf_rate": dtmf_rate,
        "avg_duration": round(avg_dur, 1),
        "median_duration": round(median_dur, 1),
        "positive_response": positive_count,
        "positive_rate": positive_rate,
    }

    # hourly volume
    hours = list(range(24))
    total_by_hour = df["Hour"].value_counts().reindex(hours, fill_value=0)
    connected_by_hour = connected["Call Hour"].value_counts().reindex(hours, fill_value=0)
    hourly = {"hours": hours, "total": total_by_hour.tolist(), "connected": connected_by_hour.tolist()}

    # duration histogram
    if len(conn_dur):
        hist_bucket = pd.cut(conn_dur["TOTAL DURATION(In Seconds)"], bins=DURATION_HIST_BINS,
                              labels=DURATION_HIST_LABELS, right=False)
        hist_counts = hist_bucket.value_counts().reindex(DURATION_HIST_LABELS, fill_value=0)
    else:
        hist_counts = pd.Series([0] * len(DURATION_HIST_LABELS), index=DURATION_HIST_LABELS)
    duration_hist = {"labels": DURATION_HIST_LABELS, "counts": hist_counts.tolist()}

    # duration by hour
    if len(conn_dur):
        dur_by_hour = conn_dur.groupby("Call Hour")["TOTAL DURATION(In Seconds)"].agg(["mean", "median", "count"])
        dur_by_hour = dur_by_hour.reindex(hours, fill_value=0)
    else:
        dur_by_hour = pd.DataFrame({"mean": [0] * 24, "median": [0] * 24, "count": [0] * 24}, index=hours)
    duration_by_hour = {
        "hours": hours,
        "mean": [round(v, 1) for v in dur_by_hour["mean"].tolist()],
        "median": [round(v, 1) for v in dur_by_hour["median"].tolist()],
        "count": dur_by_hour["count"].astype(int).tolist(),
    }

    # talk time buckets
    if len(conn_dur):
        tb = pd.cut(conn_dur["TOTAL DURATION(In Seconds)"], bins=TALK_BUCKETS, labels=TALK_BUCKET_LABELS, right=False)
        tb_counts = tb.value_counts().reindex(TALK_BUCKET_LABELS, fill_value=0)
    else:
        tb_counts = pd.Series([0] * len(TALK_BUCKET_LABELS), index=TALK_BUCKET_LABELS)
    talk_time_buckets = {
        "labels": TALK_BUCKET_LABELS,
        "counts": tb_counts.tolist(),
        "pct": [round(v / tb_counts.sum() * 100, 1) if tb_counts.sum() else 0 for v in tb_counts.tolist()],
    }

    # DTMF keypad distribution
    dtmf_vals = connected["DTMF_num"].dropna()
    if len(dtmf_vals):
        digits = sorted(dtmf_vals.astype(int).unique().tolist())
        counts = [int((dtmf_vals.astype(int) == d).sum()) for d in digits]
    else:
        digits, counts = [], []
    dtmf_keypad = {"digits": digits, "counts": counts}

    # daily trend (only meaningful if date range spans >1 day)
    daily = df.groupby("Date").agg(total=("A PARTY DIAL STATUS", "count")).reset_index()
    daily_connected = connected.groupby(connected["Dial Start"].dt.date).size()
    daily_dtmf = connected[connected["DTMF_num"].notna()].groupby(
        connected[connected["DTMF_num"].notna()]["Dial Start"].dt.date).size()
    daily["date_str"] = daily["Date"].astype(str)
    daily_trend = {
        "dates": daily["date_str"].tolist(),
        "total": daily["total"].tolist(),
        "connected": [int(daily_connected.get(d, 0)) for d in daily["Date"]],
        "dtmf": [int(daily_dtmf.get(d, 0)) for d in daily["Date"]],
    }

    # weekday breakdown
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    wd_total = df["Weekday"].value_counts().reindex(weekday_order, fill_value=0)
    wd_connected = connected["Weekday"].value_counts().reindex(weekday_order, fill_value=0)
    weekday_breakdown = {
        "labels": [w[:3] for w in weekday_order],
        "total": wd_total.tolist(),
        "connected": wd_connected.tolist(),
    }

    # dip vs peak hours (status mix comparison), mirrors the district-report Task 4 chart
    dip_vs_peak = None
    eligible_hours = total_by_hour[total_by_hour >= max(20, total_attempts * 0.01)].index
    if len(eligible_hours) >= 4:
        conn_by_hour_full = connected_by_hour.reindex(eligible_hours, fill_value=0)
        dip_hours = sorted(conn_by_hour_full.nsmallest(3).index.tolist())
        peak_hours = sorted(conn_by_hour_full.nlargest(3).index.tolist())
        ordered_hours = dip_hours + [h for h in peak_hours if h not in dip_hours]
        status_by_hour_ct = pd.crosstab(df["Hour"], df["A PARTY DIAL STATUS"])
        status_by_hour_pct = status_by_hour_ct.div(status_by_hour_ct.sum(axis=1), axis=0) * 100
        series = {}
        for s in STATUS_ORDER:
            if s in status_by_hour_pct.columns:
                series[s] = [round(float(status_by_hour_pct.loc[h, s]), 1) if h in status_by_hour_pct.index else 0
                             for h in ordered_hours]
        dip_vs_peak = {
            "hours": [f"{h}:00" for h in ordered_hours],
            "n_dip": len(dip_hours),
            "attempts": [int(total_by_hour.get(h, 0)) for h in ordered_hours],
            "series": series,
        }

    insights = build_insights(df, connected, conn_dur, kpis)

    return {
        "kpis": kpis,
        "status_breakdown": status_breakdown,
        "hourly": hourly,
        "duration_hist": duration_hist,
        "duration_by_hour": duration_by_hour,
        "talk_time_buckets": talk_time_buckets,
        "dtmf_keypad": dtmf_keypad,
        "daily_trend": daily_trend,
        "weekday_breakdown": weekday_breakdown,
        "dip_vs_peak": dip_vs_peak,
        "insights": insights,
    }


# ------------------------------------------------------------------
# Rule-based insight / diagnosis engine
# ------------------------------------------------------------------
def build_insights(df, connected, conn_dur, kpis):
    insights = []
    total = len(df)
    if total == 0:
        return insights

    # ---- 1. Timeout diagnosis ----
    timeout_df = df[df["A PARTY DIAL STATUS"] == "Timeout"]
    if len(timeout_df):
        ring = timeout_df["Dial Duration (sec)"].dropna()
        if len(ring):
            med_ring = ring.median()
            insights.append({
                "category": "Timeout",
                "title": f"Calls ring for ~{med_ring:.0f}s before timing out",
                "body": (f"Median ring duration on {len(timeout_df):,} timed-out attempts is {med_ring:.0f}s "
                         f"(mean {ring.mean():.0f}s). This looks like a fixed system ring-timeout rather than "
                         f"random non-answer — if the threshold can be extended a few seconds, some of these "
                         f"may convert to connects."),
            })
        by_hour = timeout_df["Hour"].value_counts()
        attempts_by_hour = df["Hour"].value_counts()
        rate_by_hour = (by_hour / attempts_by_hour * 100).dropna().sort_values(ascending=False)
        rate_by_hour = rate_by_hour[attempts_by_hour.reindex(rate_by_hour.index) >= max(20, total * 0.005)]
        if len(rate_by_hour):
            top = rate_by_hour.head(3)
            hrs = ", ".join(f"{int(h)}:00 ({v:.0f}%)" for h, v in top.items())
            insights.append({
                "category": "Timeout",
                "title": "Timeout rate is highest at specific hours",
                "body": f"Hours with the highest timeout share of attempts: {hrs}. These are candidates for "
                        f"redial retries later in the day rather than immediate re-attempts.",
            })

    # ---- 2. Connected-but-no-DTMF diagnosis ----
    if len(connected):
        no_dtmf = connected[connected["DTMF_num"].isna()].dropna(subset=["TOTAL DURATION(In Seconds)"])
        pressed = connected[connected["DTMF_num"].notna()].dropna(subset=["TOTAL DURATION(In Seconds)"])
        if len(no_dtmf):
            bins = [0, 10, 20, 30, 60, np.inf]
            labels = ["<10s (hung up before prompt)", "10-20s (prompt likely playing)",
                      "20-30s (heard prompt, no press)", "30-60s", "60s+ (listened fully, no press)"]
            bucketed = pd.cut(no_dtmf["TOTAL DURATION(In Seconds)"], bins=bins, labels=labels, right=False)
            bucket_pct = (bucketed.value_counts(normalize=True) * 100).reindex(labels, fill_value=0)
            dominant = bucket_pct.idxmax()
            dominant_pct = bucket_pct.max()
            avg_no_dtmf = no_dtmf["TOTAL DURATION(In Seconds)"].mean()
            avg_pressed = pressed["TOTAL DURATION(In Seconds)"].mean() if len(pressed) else None
            body = (f"{dominant_pct:.0f}% of connected-but-no-DTMF calls fall in the '{dominant}' bucket "
                    f"(avg duration {avg_no_dtmf:.0f}s")
            if avg_pressed is not None:
                body += f", vs {avg_pressed:.0f}s average for calls where DTMF was pressed)."
            else:
                body += ")."
            if dominant.startswith("<10s"):
                body += " Most non-responders are dropping off before they even reach the keypad prompt — consider shortening the intro message or moving the DTMF ask earlier."
            elif dominant.startswith("60s+"):
                body += " Most non-responders listen to the full message but still don't press — the option choices or ask may not be clear enough, or the call may be reaching voicemail/an automated line."
            else:
                body += " Most non-responders hang up mid-message — consider trimming the message before the keypad prompt."
            insights.append({"category": "DTMF Engagement", "title": "Why connected calls aren't producing a DTMF press", "body": body})

    # ---- 3. Busy / Line Drop patterns ----
    for status in ["Busy", "Line Drop"]:
        sub = df[df["A PARTY DIAL STATUS"] == status]
        if len(sub) < max(30, total * 0.01):
            continue
        by_hour = sub["Hour"].value_counts()
        attempts_by_hour = df["Hour"].value_counts()
        rate_by_hour = (by_hour / attempts_by_hour * 100).dropna().sort_values(ascending=False)
        rate_by_hour = rate_by_hour[attempts_by_hour.reindex(rate_by_hour.index) >= max(20, total * 0.005)]
        if len(rate_by_hour):
            top = rate_by_hour.head(2)
            hrs = ", ".join(f"{int(h)}:00 ({v:.0f}%)" for h, v in top.items())
            insights.append({
                "category": status,
                "title": f"{status} concentrates at specific hours",
                "body": f"{status} accounts for a disproportionate share of attempts at {hrs}. "
                        f"Shifting some attempts away from these hours may reduce this failure mode.",
            })

    # ---- 4. Best calling window recommendation ----
    attempts_by_hour = df["Hour"].value_counts()
    connect_by_hour = df.assign(is_conn=df["A PARTY DIAL STATUS"] == "Connected").groupby("Hour")["is_conn"].mean() * 100
    eligible_hours = attempts_by_hour[attempts_by_hour >= max(20, total * 0.01)].index
    if len(eligible_hours):
        best = connect_by_hour.reindex(eligible_hours).sort_values(ascending=False).head(3)
        hrs = ", ".join(f"{int(h)}:00 ({v:.0f}% connect)" for h, v in best.items())
        insights.append({
            "category": "Best Window",
            "title": "Best-performing hours for connect rate",
            "body": f"{hrs}. Weighting more dial attempts toward these hours (and away from the low-connect "
                    f"hours flagged above) should raise the overall connect rate without adding attempts.",
        })

    # ---- 5. DTMF engagement by hour ----
    if len(connected):
        dtmf_by_hour = connected.assign(has_dtmf=connected["DTMF_num"].notna()).groupby("Call Hour")["has_dtmf"].mean() * 100
        conn_by_hour_counts = connected["Call Hour"].value_counts()
        eligible = conn_by_hour_counts[conn_by_hour_counts >= max(20, len(connected) * 0.01)].index
        if len(eligible):
            best_eng = dtmf_by_hour.reindex(eligible).sort_values(ascending=False).head(2)
            if len(best_eng):
                hrs = ", ".join(f"{int(h)}:00 ({v:.0f}%)" for h, v in best_eng.items())
                insights.append({
                    "category": "Best Window",
                    "title": "Hours with the strongest DTMF engagement",
                    "body": f"Connected calls made around {hrs} are the most likely to result in a keypad "
                            f"response — a good window for campaigns where the DTMF action itself is the KPI.",
                })

    return insights


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/csvs")
def api_csvs():
    return jsonify(list_available_csvs())


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Please upload a .csv file"}), 400
    safe_name = f"{uuid.uuid4().hex[:8]}_{os.path.basename(f.filename)}"
    path = os.path.join(UPLOAD_DIR, safe_name)
    f.save(path)
    try:
        load_and_clean(path)  # validates schema early
    except Exception as e:  # noqa: BLE001
        os.remove(path)
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": safe_name, "filename": f.filename})


@app.route("/api/dashboard")
def api_dashboard():
    csv_id = request.args.get("csv")
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    if not csv_id:
        return jsonify({"error": "csv parameter required"}), 400
    try:
        path = resolve_csv_path(csv_id)
        df = load_and_clean(path)
    except FileNotFoundError:
        return jsonify({"error": f"CSV not found: {csv_id}"}), 404
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400

    filtered = filter_by_date(df, start_date, end_date)
    if len(filtered) == 0:
        return jsonify({"error": "No rows in the selected date range"}), 400

    payload = compute_dashboard(filtered)
    valid_dates = df["Date"].dropna()
    payload["meta"] = {
        "filename": csv_id,
        "rows": int(len(filtered)),
        "date_min": str(valid_dates.min()) if len(valid_dates) else None,
        "date_max": str(valid_dates.max()) if len(valid_dates) else None,
    }
    return jsonify(payload)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
