"""Use TSP's installed, published mainland exchange calendar preset."""
from datetime import date, datetime, timedelta

from tivelo_contracts.errors import TiveloError
from tivelo_market_cn.calendar import CNCalendar
from tivelo_market_cn.presets import compile_calendar, installed_calendar_presets

from .clock import CN


def confirmed_sessions(now: datetime) -> tuple[tuple[str, ...], str]:
    """Return confirmed joint sessions around today, or fail with a coverage error."""
    today = now.astimezone(CN).date()
    start = today - timedelta(days=100)
    venues = {'XSHG', 'XSHE'}
    needed = {(venue, year) for venue in venues for year in {start.year, today.year}}
    for preset in installed_calendar_presets():
        if not needed <= {(y.venue_id, y.year) for y in preset.contents.years}:
            continue
        end = min(today + timedelta(days=14), date(today.year, 12, 31))
        sets = []
        try:
            for venue in sorted(venues):
                version = compile_calendar(preset, venue_id=venue, start=start, end=end)
                calendar = CNCalendar(version)
                sets.append({d.trading_date.isoformat() for d in calendar.trading_days(start, end, as_of=now)})
        except TiveloError:
            continue
        return tuple(sorted(sets[0] & sets[1])), preset.contents.preset_version
    raise ValueError('已安装的沪深交易日历未覆盖当前日期')
