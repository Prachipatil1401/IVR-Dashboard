"""Generate a synthetic IVR call-log CSV for testing the dashboard."""
import csv
import random
from datetime import datetime, timedelta

random.seed(42)
OUT = "sample_calls.csv"
N_DAYS = 6
START = datetime(2026, 7, 2)

statuses = ["Connected", "Timeout", "Busy", "User Not Responding", "Line Drop"]
status_weights = [0.44, 0.42, 0.02, 0.11, 0.01]

rows = []
call_id = 1
for day in range(N_DAYS):
    date = START + timedelta(days=day)
    daily_calls = random.randint(60000, 145000)
    for _ in range(daily_calls):
        hour = random.choices(range(24), weights=[0]*8 + [80,70,65,68,70,65,35,25,25,30,35,55,70,90] + [0]*2)[0]
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        ts = date.replace(hour=hour, minute=minute, second=second)
        status = random.choices(statuses, weights=status_weights)[0]
        duration = ""
        dtmf = ""
        if status == "Connected":
            duration = max(1, int(random.gauss(35, 18)))
            if random.random() < 0.115:
                dtmf = random.choices([1, 2, 3, 4, 5], weights=[30, 20, 27, 12, 18])[0]
        phone = f"9{random.randint(100000000, 999999999)}"
        rows.append([call_id, phone, ts.strftime("%Y-%m-%d %H:%M:%S"), status, duration, dtmf])
        call_id += 1

with open(OUT, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["call_id", "phone_number", "call_datetime", "status", "duration_sec", "dtmf_key"])
    writer.writerows(rows)

print(f"Wrote {len(rows):,} rows to {OUT}")
