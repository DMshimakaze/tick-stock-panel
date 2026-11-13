"""Native 15m evidence only. A native candle never claims three received 5m bars."""

from .market_grid import FIELDS, normalize_five, as_ms, combined, wall

SOURCE = "TICKFLOW_NATIVE_15M"


def normalize_native(data, start, end):
    # The provider can include a currently forming candle even with end_time.
    stamps = data["timestamp"]
    if any(len(data[k]) != len(stamps) for k in (*FIELDS[:5], "timestamp")):
        raise ValueError("TRUNCATED_OR_MISALIGNED_NATIVE15")
    keep = [i for i, stamp in enumerate(stamps) if stamp <= as_ms(end)]
    selected = {k: [values[i] for i in keep] for k, values in data.items() if k in ("timestamp", *FIELDS)}
    rows, invalid = normalize_five(selected, start, end)
    if any(wall(t).minute % 15 for t in rows):
        raise ValueError("NATIVE15_NOT_CLOSE_LABELLED")
    return [combined([r], t, SOURCE, 1) for t, r in sorted(rows.items())], dict(
        invalid=invalid, unfinished=len(stamps) - len(keep), source_count=1,
        completeness="NATIVE_15M_COMPLETED", production_admitted=False,
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
