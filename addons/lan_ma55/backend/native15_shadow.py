"""TickFlow completed native input; shared by production and bounded comparisons."""

from math import isfinite

from .market_grid import FIELDS, normalize_five, as_ms, combined, wall

SOURCE = "TICKFLOW_NATIVE_15M"
PROOF = "NATIVE_15M_COMPLETED"


def normalize_native(data, start, end):
    # The provider can include a currently forming candle even with end_time.
    stamps = data["timestamp"]
    if any(len(data[k]) != len(stamps) for k in (*FIELDS[:5], "timestamp")):
        raise ValueError("TRUNCATED_OR_MISALIGNED_NATIVE15")
    if any(type(t) not in (int, float) or not isfinite(t) for t in stamps):
        raise ValueError("INVALID_NATIVE15_TIMESTAMP")
    keep = [i for i, stamp in enumerate(stamps) if stamp <= as_ms(end)]
    selected = {k: [values[i] for i in keep] for k, values in data.items() if k in ("timestamp", *FIELDS)}
    # Reject malformed rows before compact lots are multiplied by 100.
    for key in FIELDS:
        if key in selected and any(v is not None and (type(v) not in (int, float) or not isfinite(v)) for v in selected[key]):
            raise ValueError("INVALID_NATIVE15_OHLCV_TYPE")
    try:
        rows, invalid = normalize_five(selected, start, end)
    except ValueError as exc:
        raise ValueError(str(exc).replace("5M", "NATIVE15")) from None
    if any(wall(t).minute % 15 for t in (*rows, *invalid)):
        raise ValueError("NATIVE15_NOT_CLOSE_LABELLED")
    return [dict(combined([r], t, SOURCE, 1), source_proof=PROOF) for t, r in sorted(rows.items())], dict(
        invalid=invalid, unfinished=len(stamps) - len(keep), source_count=1,
        completeness=PROOF, production_admitted=True,
    )


def compare_bars(left, right):
    """Report every numeric difference without price rounding or wider tolerances."""
    a, b = ({r["timestamp"]: r for r in rows} for rows in (left, right))
    shared = sorted(a.keys() & b.keys())
    differences, maximum = [], {k: 0 for k in FIELDS}
    for at in shared:
        for field in FIELDS:
            x, y = a[at].get(field), b[at].get(field)
            if x == y:
                continue
            delta = None if x is None or y is None else abs(x - y)
            if delta is not None:
                maximum[field] = max(maximum[field], delta)
            differences.append(dict(at=at, field=field, left=x, right=y, absolute=delta))
    return dict(aligned=len(shared), missing_native=sorted(a.keys() - b.keys()),
                extra_native=sorted(b.keys() - a.keys()),
                different_bars=len({r["at"] for r in differences}),
                different_fields=len(differences), maximum_absolute=maximum,
                differences=differences)
