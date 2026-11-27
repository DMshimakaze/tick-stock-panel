"""Apply calendar-approved source data and persist alerts from one live projection."""
from datetime import datetime

from .clock import CN, completed_through
from .energy import Config, alert_direction
from .projection import build_view


def save_closed(store, records, now, sessions):
    today = now.astimezone(CN).date().isoformat()
    changed = False
    for day in records:
        if day.date not in sessions or day.date > today:
            continue
        if day.date == today and (now.hour, now.minute) < (9, 25):
            continue
        cutoff = completed_through(now) if day.date == today else 239
        changed = store.save(day, cutoff) or changed
    return changed


def record_alerts(store, view, config):
    if view['mode'] != 'LIVE':
        return
    day = view['trade_date']
    for row in view['indices']:
        metrics = row['metrics']
        if row['status'] == 'LIVE':
            direction = alert_direction(metrics, config)
            if direction:
                store.record_event(day, row['symbol'], direction, datetime.fromisoformat(row['stamp']),
                                   metrics, config.cooldown_minutes)
    view['events'] = store.events(day)


def publish_cycle(store, *, now, day, previous, prior_sessions, quotes, publish,
                  live, config=Config(), history_source='tencent', compatible_snapshot_sources=('tencent',)):
    view = build_view(store, day=day, previous=previous, prior_sessions=prior_sessions, now=now,
                      quotes=quotes, live=live, config=config, history_source=history_source,
                      compatible_snapshot_sources=compatible_snapshot_sources)
    record_alerts(store, view, config)
    publish(view)
    return view
