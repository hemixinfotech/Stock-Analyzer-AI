import json
from pathlib import Path
import pandas as pd
import yfinance as yf

# Load results JSON
infile = Path('results_nifty_trend.json')
if not infile.exists():
    raise SystemExit('results_nifty_trend.json not found in repo root')

r = json.load(open(infile, 'r', encoding='utf-8'))
records = []
for rec in r.get('records', []):
    t = rec.get('ticker')
    trend = rec.get('trend', '')
    idx = rec.get('index', '')
    name = ''
    try:
        info = yf.Ticker(t).info
        name = info.get('longName') or info.get('shortName') or ''
    except Exception:
        name = ''
    if not name:
        name = (t or '').split('.')[0]
    records.append({'ticker': t, 'name': name, 'trend': trend, 'index': idx})

if not records:
    print('No records found in results_nifty_trend.json')
    raise SystemExit(0)

df = pd.DataFrame(records)

# Ensure configs directory exists
out_dir = Path('configs')
out_dir.mkdir(exist_ok=True)

# Focused sheets for midcap and smallcap
midcap_df = df[df['index'].str.contains('midcap', case=False, na=False)].copy()
# configs may use 'small' or 'smallcap' in the index name (e.g., 'nifty_small_100')
smallcap_df = df[df['index'].str.contains('smallcap|small', case=False, na=False)].copy()

out_xlsx = out_dir / 'results_trend.xlsx'

# Try writing Excel with separate sheets; if engine missing, fallback to separate CSV files
try:
    with pd.ExcelWriter(out_xlsx, engine='openpyxl') as writer:
        if not midcap_df.empty:
            midcap_df.to_excel(writer, sheet_name='Midcap 100', index=False)
        if not smallcap_df.empty:
            smallcap_df.to_excel(writer, sheet_name='Smallcap 100', index=False)
        # also write an 'All' sheet
        df.to_excel(writer, sheet_name='All Indices', index=False)
    print(f'Wrote Excel workbook: {out_xlsx} (sheets: {"Midcap 100" if not midcap_df.empty else ""} {"Smallcap 100" if not smallcap_df.empty else ""})')
except Exception as e:
    print('Excel write failed, falling back to CSV files:', e)
    if not midcap_df.empty:
        midcap_csv = out_dir / 'results_trend_midcap.csv'
        midcap_df.to_csv(midcap_csv, index=False)
        print('Wrote', midcap_csv)
    if not smallcap_df.empty:
        smallcap_csv = out_dir / 'results_trend_smallcap.csv'
        smallcap_df.to_csv(smallcap_csv, index=False)
        print('Wrote', smallcap_csv)
    # write full CSV as well
    full_csv = out_dir / 'results_trend_all.csv'
    df.to_csv(full_csv, index=False)
    print('Wrote', full_csv)
