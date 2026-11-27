"""Source parsers and small-batch client. Turnover is normalized to yuan."""
import re
from dataclasses import dataclass
from datetime import datetime
from .clock import CN, closed_index, position
from .energy import valid

SYMBOLS = ('000001.SH', '399106.SZ', '399001.SZ', '399006.SZ', '000680.SH')
NAMES = dict(zip(SYMBOLS, ('上证指数', '深证综指', '深证成指', '创业板指', '科创综指')))
DISPLAY = ('MARKET_TOTAL', '000001.SH', '399001.SZ', '399006.SZ', '000680.SH')


def number(value):
    try:
        if isinstance(value, bool):
            return None
        n = float(value)
        return n if valid(n) else None
    except (TypeError, ValueError):
        return None


def tx_code(symbol):
    if symbol not in SYMBOLS:
        raise ValueError('index outside configured scope')
    code, exchange = symbol.split('.')
    return exchange.lower() + code


@dataclass(frozen=True)
class Quote:
    symbol: str
    stamp: datetime
    amount: float
    price: float | None
    change_pct: float | None
    source: str


@dataclass(frozen=True)
class Day:
    symbol: str
    date: str
    source: str
    auction: float | None
    amounts: dict[int, float]
    prices: dict[int, float]


def _quote(symbol, timestamp, amount, price, previous, source):
    if amount is None or price is None or price <= 0:
        return None
    return Quote(symbol, timestamp.replace(tzinfo=CN), amount, price,
                 (price / previous - 1) * 100 if previous and previous > 0 else None, source)


def parse_tencent_quotes(text):
    out = {}
    for line in text.splitlines():
        match = re.fullmatch(r'v_(sh|sz)(\d{6})="(.+)";?', line.strip())
        if not match:
            continue
        ex, code, payload = match.groups()
        symbol, fields = code + '.' + ex.upper(), payload.split('~')
        if symbol not in SYMBOLS or len(fields) < 38:
            continue
        parts = fields[35].split('/')
        amount = number(parts[2]) if len(parts) >= 3 else None
        if amount is None:
            wan = number(fields[37])
            amount = wan * 10000 if wan is not None else None
        try:
            stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S')
        except ValueError:
            continue
        quote = _quote(symbol, stamp, amount, number(fields[3]), number(fields[4]), 'tencent')
        if quote:
            out[symbol] = quote
    return out


def parse_sina_quotes(text):
    out = {}
    for line in text.splitlines():
        match = re.fullmatch(r'var hq_str_(sh|sz)(\d{6})="(.+)";?', line.strip())
        if not match:
            continue
        ex, code, payload = match.groups()
        symbol, fields = code + '.' + ex.upper(), payload.split(',')
        if symbol not in SYMBOLS or len(fields) < 32:
            continue
        try:
            stamp = datetime.strptime(fields[30] + ' ' + fields[31], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        quote = _quote(symbol, stamp, number(fields[9]), number(fields[3]), number(fields[2]), 'sina')
        if quote:
            out[symbol] = quote
    return out


def parse_tencent_days(payload, symbol):
    # The single-session endpoint wraps one record; the five-day endpoint returns a list.
    entries = (payload.get('data') or {}).get(tx_code(symbol), {}).get('data') or []
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for entry in entries:
        try:
            day = datetime.strptime(str(entry['date']).replace('-', ''), '%Y%m%d').date().isoformat()
        except (KeyError, ValueError):
            continue
        bars, prices, auction = {}, {}, None
        for row in entry.get('data') or ():
            fields = str(row).split()
            if len(fields) < 4:
                continue
            label, price, amount = fields[0], number(fields[1]), number(fields[3])
            if amount is None or price is None or price <= 0:
                continue
            if label == '0930':
                if auction is None:
                    auction = amount
                continue
            k = closed_index(label)
            if k is not None and k not in bars:
                bars[k], prices[k] = amount, price
        if bars or auction is not None:
            out.append(Day(symbol, day, 'tencent', auction, dict(sorted(bars.items())), prices))
    return sorted(out, key=lambda d: d.date, reverse=True)


def parse_eastmoney_days(payload, symbol):
    """Unlike Tencent, trend amounts are increments. A missing prefix/gap makes
    subsequent cumulative sums unknown; do not silently turn missing bars into 0."""
    grouped = {}
    for row in (payload.get('data') or {}).get('trends', []):
        fields = str(row).split(',')
        if len(fields) < 7:
            continue
        try:
            stamp = datetime.strptime(fields[0], '%Y-%m-%d %H:%M')
        except ValueError:
            continue
        label, price, amount = stamp.strftime('%H:%M'), number(fields[2]), number(fields[6])
        if price is None or price <= 0 or amount is None:
            continue
        grouped.setdefault(stamp.date().isoformat(), {}).setdefault(label, (price, amount))
    out = []
    for day, rows in grouped.items():
        auction = rows.get('09:30', (None, None))[1]
        bars, prices = {}, {}
        if auction is not None:
            total = auction
            by_index = {closed_index(label): pair for label, pair in rows.items() if closed_index(label) is not None}
            for k in range(240):
                if k not in by_index:
                    break
                price, delta = by_index[k]
                total += delta
                bars[k], prices[k] = total, price
        out.append(Day(symbol, day, 'eastmoney', auction, bars, prices))
    return sorted(out, key=lambda d: d.date, reverse=True)


def aggregate_quote(quotes, max_drift_seconds=10):
    sh, sz = quotes.get('000001.SH'), quotes.get('399106.SZ')
    if not sh or not sz:
        return None
    sp, zp = position(sh.stamp), position(sz.stamp)
    if (sh.stamp.date() != sz.stamp.date() or sh.source != sz.source
            or sp is None or zp is None or sp[0] != zp[0]
            or abs((sh.stamp - sz.stamp).total_seconds()) > max_drift_seconds):
        return None
    return Quote('MARKET_TOTAL', max(sh.stamp, sz.stamp), sh.amount + sz.amount,
                 None, None, sh.source)


class Client:
    """IO only. Caller owns trading calendar, rate budget and retry cadence."""
    def __init__(self, http):
        self.http = http

    async def quotes(self, source='tencent'):
        codes = ','.join(map(tx_code, SYMBOLS))
        if source == 'tencent':
            response = await self.http.get('https://qt.gtimg.cn/q=' + codes,
                                           headers={'Referer': 'https://gu.qq.com/'})
            parse = parse_tencent_quotes
        elif source == 'sina':
            response = await self.http.get('https://hq.sinajs.cn/list=' + codes,
                                           headers={'Referer': 'https://finance.sina.com.cn/'})
            parse = parse_sina_quotes
        else:
            raise ValueError('unsupported snapshot source')
        response.raise_for_status()
        result = parse(response.content.decode('gb18030', errors='replace'))
        if not result:
            raise ValueError('empty quote response')
        return result

    async def days(self, symbol, source='tencent', *, deep=True):
        code = tx_code(symbol)
        if source == 'tencent':
            endpoint = 'day' if deep else 'minute'
            response = await self.http.get(f'https://web.ifzq.gtimg.cn/appstock/app/{endpoint}/query',
                                           params={'code': code}, headers={'Referer': 'https://gu.qq.com/'})
            parse = parse_tencent_days
        elif source == 'eastmoney':
            response = await self.http.get('https://push2his.eastmoney.com/api/qt/stock/trends2/get', params={
                'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58', 'ndays': 2, 'iscr': 1,
                'secid': ('1.' if symbol.endswith('.SH') else '0.') + symbol[:6]},
                headers={'Referer': 'https://quote.eastmoney.com/'})
            parse = parse_eastmoney_days
        else:
            raise ValueError('unsupported history source')
        response.raise_for_status()
        result = parse(response.json(), symbol)
        if not result:
            raise ValueError('empty minute response')
        return result
