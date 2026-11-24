"""Verbatim minimal_features from research 4440e198; stdlib only."""
from math import fsum
from statistics import median

def minimal_features(history, bar, slot_volumes):
    """V0.2 features; history is the prior <=55 valid bars, slots the prior <=5 days.

    A previous MA55 needs 55 earlier closes. A supplied stored MA never fills a
    missing causal window. Same-clock median needs three samples, as frozen.
    """
    nan=float('nan');rows=history+[bar]
    known=fsum(r['close'] for r in history[-55:])/55 if len(history)>=55 else nan
    ma=fsum(r['close'] for r in rows[-55:])/55 if len(rows)>=55 else nan
    baseline=median(r['volume'] for r in history[-20:]) if len(history)>=20 else nan
    slot_base=median(slot_volumes[-5:]) if len(slot_volumes)>=3 else nan
    tr=[max(r['high']-r['low'],abs(r['high']-rows[i-1]['close']),abs(r['low']-rows[i-1]['close']))
        if i else r['high']-r['low'] for i,r in enumerate(rows)]
    return dict(bar, known_ma55=known,ma55=ma,
        range16=max(r['high'] for r in rows[-16:])/min(r['low'] for r in rows[-16:])-1 if len(rows)>=16 else nan,
        volume_baseline=baseline,vol20=bar['volume']/baseline if baseline>0 else nan,
        vol_slot=bar['volume']/slot_base if slot_base>0 else nan,
        slot_baseline=slot_base,atr20=fsum(tr[-20:])/20 if len(tr)>=20 else nan)
