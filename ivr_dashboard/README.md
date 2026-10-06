# IVR Campaign Dashboard (local)

A local Flask app that turns any `DCS_<district>*.csv` file (same schema as
your Colab script) into an interactive dashboard: KPI cards, hourly/duration
charts, DTMF engagement, failure analysis, and a rule-based **Insights &
Recommendations** panel that flags *why* calls are timing out, why connected
calls aren't producing a DTMF press, which hours are worst for busy/line-drop,
and which hours perform best — so the team can decide where to shift dial
attempts.

## 1. Install

```bash
cd ivr_dashboard
python -m venv venv          # optional but recommended
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Add data

Two ways to get a CSV into the dashboard:

- **Drop it in `data/`** before starting the app (e.g. `data/DCS_gadchiroli1.csv`).
- **Upload it from the browser** — the "Upload CSV" button in the top bar
  works with any day's or week's file that has the same columns (uploaded
  files land in `uploads/`).

The CSV must have at least these columns (same as your existing script):
`A PARTY DIAL STATUS`, `A PARTY DIAL START DATE`, `A PARTY DIAL START TIME`,
`A PARTY DIAL END DATE`, `A PARTY DIAL END TIME`, `A PARTY CALL START TIME`,
`TOTAL DURATION(In Seconds)`, and ideally `DTMF`. Extra columns (B-party
fields, campaign name, etc.) are dropped automatically, same as the cleaning
step in your Colab script.

## 3. Run

```bash
python app.py
```

Open **http://127.0.0.1:5000** in a browser. Pick a CSV from the dropdown,
optionally narrow the date range (works for a single day or a full week in
one file), and click **Apply**.

## What each tab shows

- **Executive Summary** — top-line KPIs, status distribution, daily trend
  (if the file spans multiple days), volume by weekday.
- **Time & Duration** — call volume by hour, duration by hour, duration
  histogram.
- **DTMF Engagement** — pressed vs. not-pressed, keypad digit breakdown,
  talk-time buckets.
- **Failure Analysis** — failure reason counts, and a dip-vs-peak-hour status
  mix chart (same idea as Task 4 in your district reports).
- **Insights & Recommendations** — auto-generated, data-driven notes, e.g.:
  - *Timeout*: typical ring duration before timeout, and which hours have
    the highest timeout share (candidates for later redial).
  - *DTMF Engagement*: whether non-responders are hanging up before the
    prompt finishes, or listening fully without pressing — with a
    recommendation for each case.
  - *Busy / Line Drop*: hours where each failure mode concentrates.
  - *Best Window*: hours with the highest connect rate and highest DTMF
    engagement, to weight future dial attempts toward.
- **Call Records** — searchable, paginated table of the underlying rows for
  the current filter.

## Notes

- Everything runs locally — no data leaves your machine.
- The insight engine is rule-based on the columns your CSVs actually have
  (dial status, timing, duration, DTMF). If a district's CSV has extra
  columns (e.g. campaign name, taluka/village), let me know the exact column
  names and I can add filters/breakdowns for those too — right now the
  dashboard focuses on what's consistently present across your district
  files.
- Large files (400k+ rows) load fine; the app caches a cleaned copy per file
  so switching date ranges is instant. Re-uploading a changed file with the
  same name creates a new copy (cache keys off file modified-time), so no
  stale-data surprises.
