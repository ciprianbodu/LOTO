"""Test integration TimesFM v2 engine."""
import sys, os
sys.path.insert(0, r'd:\_LIBRARIES\OneDrive\_CODING\_LOTO')
os.chdir(r'd:\_LIBRARIES\OneDrive\_CODING\_LOTO')

from loto_engine import LotoEngine

csv = 'loto_6_49.csv'
if not os.path.exists(csv):
    from loto_engine import create_demo_data
    create_demo_data('test_demo.csv', '6/49')
    csv = 'test_demo.csv'

engine = LotoEngine('6/49')
ok = engine.load_data(csv)
print(f'Data loaded: {ok}, rows: {len(engine.data)}')

print('Testing _get_timesfm_scores (v2)...')
scores = engine._get_timesfm_scores(context_len=min(len(engine.data), 200))
if scores:
    top5 = sorted(scores.items(), key=lambda x: -x[1])[:5]
    print(f'SUCCESS! Top 5 NQI scores: {top5}')
    audit_keys = list(engine.audit.keys())
    print(f'Audit keys: {audit_keys}')
    xreg = engine.audit.get('timesfm_xreg')
    if xreg:
        print(f'XReg covariates: {xreg}')
    anomaly = engine.audit.get('timesfm_anomaly_scores')
    if anomaly:
        a_top = sorted(anomaly.items(), key=lambda x: -x[1])[:5]
        print(f'Top 5 anomaly: {a_top}')
else:
    print('No scores returned')
    print(f'Audit: {engine.audit}')
