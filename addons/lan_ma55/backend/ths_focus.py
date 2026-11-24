"""THS membership transport adapter; no quotes, credentials or worker lifecycle.

request(path, params) is an authenticated, budgeted GET returning decoded JSON.
The owner supplies the authorized transport and scheduling; this module never
retries or switches providers. The public contract is the THS Fuyao index API.
"""
from .focus_pool import KINDS, KEY, ScopeConflict, decode_catalog, decode_members

CATALOG = "/api/a-share-index/catalog/ths-index-list"
MEMBERS = "/api/a-share-index/constituents/ths-stock-list"


def load_catalog(request):
    entries, timestamps = {}, {}
    for kind in KINDS:
        decoded = decode_catalog(kind, request(CATALOG, {"tag": kind}))
        entries.update(decoded["entries"])
        timestamps[kind] = decoded["source_ms"]
    return dict(entries=entries, source_ms=timestamps)


def refresh_selection(pool, selection, catalog, request, *, expected_revision,
                      observed_ms, refresh_existing=False):
    """Apply changes atomically; failed retained refreshes keep old snapshots.

    Explicit save requests only newly selected boards; scheduled/manual refresh
    requests every retained board too. New-board failure commits nothing.
    Caller must expose errors and retry through its own bounded schedule.
    """
    if not isinstance(selection, (list, tuple)) or any(
        not isinstance(key, str) or not KEY.fullmatch(key) for key in selection
    ):
        raise ValueError("Invalid explicit focus selection")
    keys = list(dict.fromkeys(selection))
    old = pool.read()
    if type(expected_revision) is not int or old["revision"] != expected_revision:
        raise ScopeConflict("Focus selection changed; reread before applying")
    previous = {s["key"]: s for s in old["sectors"]}
    names = catalog["entries"]
    fresh, errors = [], {}
    for key in keys:
        before = previous.get(key)
        if before and not refresh_existing:
            continue
        try:
            if key not in names:
                raise ValueError("Board missing from verified catalog")
            snapshot = decode_members(
                key, names[key], request(MEMBERS, {"thscode": key.split(":")[1]}),
                observed_ms,
            )
            if before and (snapshot["source_ms"] < before["source_ms"] or
                           snapshot["observed_ms"] < before["observed_ms"]):
                raise ValueError("Older membership")
            fresh.append(snapshot)
        except Exception as exc:
            # Do not serialize supplier errors or credentials into UI payloads.
            errors[key] = type(exc).__name__
            if before is None:
                raise ValueError(f"New board membership unverified: {key}") from None
    result = pool.apply(keys, fresh, expected_revision=expected_revision)
    return dict(result, sector_errors=errors)
