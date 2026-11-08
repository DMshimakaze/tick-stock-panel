"""Host-owned worker: durable subscriptions, incremental first, then cold/gaps."""
import atexit
import json
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, perf_counter, time

import httpx

from .collector import Collector, SourceError
from .market import now_cn, window, projection
from .publisher import Publisher, VIEW_FIELDS
from .request_budget import CollectionStopped, RequestDeferred
from .store import Store, TABLES, TTL_SECONDS


class ProcessLease:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Another lan.kline worker owns this directory') from None

    def close(self):
        self.file.close()


def next_task(store, sessions, now):
    tasks = []
    for tracked in store.tracked():
        for period in ['5m', *(['1m'] if tracked['one_minute'] else []), '1d']:
            days, expected = window(sessions, period, now)
            if not expected:
                continue
            symbol = tracked['symbol']
            cursor = store.cursor(symbol, period)
            if not cursor:
                tasks.append((1 if period != '1d' else 2, period, expected[0], expected[-1], 'COLD', symbol))
            elif (cursor['latest'] or '') < expected[-1] and (cursor['retry_at'] <= time() or (cursor['attempted_to'] or '') < expected[-1]):
                start = next(t for t in expected if t > (cursor['latest'] or ''))
                tasks.append((0 if cursor['latest'] else 2, period, start, expected[-1], 'INCREMENT', symbol))
            elif cursor['gaps'] and cursor['retry_at'] <= time():
                tasks.append((3, period, max(expected[0], cursor['gap_start']), min(expected[-1], cursor['gap_end']), 'GAP', symbol))
    if not tasks:
        return None
    tasks.sort(key=lambda t: t[0])
    priority, period, start, end, kind, _ = tasks[0]
    symbols = [t[-1] for t in tasks if t[:5] == tasks[0][:5]][:3]
    return period, start, end, kind, symbols


class Worker:
    def __init__(self, context, settings):
        self.folder = Path(context.data_dir) / 'user_data' / 'lan_kline'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.stop, self.lock = Event(), Lock()
        self.state = dict(version='1.1.2', phase='STARTING', error=None, minute_request_limit=2,
                          database=str(self.folder/'workbench.sqlite3'), retention={'5m':75,'1m':20,'1d':300}, symbols=[])
        self.publisher = Publisher(settings['api_base'], settings.get('api_token'))
        self.collector = None
        self.thread = Thread(target=self.run, name='lan-kline-rolling', daemon=True)
        self.published = {}
        self.symbol_errors = {}
        self.revisions = {}
        self.handled_demands = {}

    def start(self):
        self.lease = ProcessLease(self.folder/'worker.lock')
        self.thread.start()
        atexit.register(self.close)

    def report(self, **values):
        with self.lock:
            self.state.update(values)
            if self.collector:
                self.state['requests'] = dict(self.collector.metrics)

    def status(self):
        with self.lock:
            return dict(self.state, worker_alive=self.thread.is_alive())

    def quota_ready(self):
        response = self.publisher.http.get('/api/extensions/lan.ma55/status')
        if response.status_code == 404:
            return True
        response.raise_for_status()
        state = response.json()
        return not state.get('backend_scanning') or state.get('minute_request_limit', 24) <= 22

    def view(self, store, demand, sessions, now):
        symbol = demand['requested_symbol']
        tracked = next((r for r in store.tracked() if r['symbol'] == symbol), None)
        cursor = store.cursor(symbol, '1m' if demand['period'] == '1m' else '1d' if demand['period'] == '1d' else '5m')
        error = self.symbol_errors.get(symbol) or self.state.get('error') or ''
        phase = 'WARMING' if not cursor else 'PARTIAL' if cursor['gaps'] else 'READY'
        if error:
            phase = 'SOURCE_PENDING'
        overview = next((r for r in store.overview() if r['symbol'] == symbol), {})
        revision = json.dumps([self.revisions.get(symbol, 0), cursor, tracked['factor_day'] if tracked else None,
                               tracked['pinned'] if tracked else 0, self.state.get('phase'), error], separators=(',',':'))
        status = dict(overview, phase=phase, quota=self.state.get('phase'), requests=self.state.get('requests', {}),
                      minute_request_limit=2, source='QuantDash Pro · 原始K落盘', error=error)
        payload = {}
        if tracked:
            metadata = json.loads(tracked['metadata'] or '{}')
            events = json.loads(tracked['events'] or 'null')
            raw = {p:[[r['end_time'],*[r[k] for k in ('open','high','low','close','volume','amount')]] for r in store.rows(symbol,p)] for p in TABLES}
            days = sorted({r[0][:10] for rows in raw.values() for r in rows})
            asof = max(days, default=now[:10])
            ratios = projection(events, days, asof)
            payload = dict(symbol=symbol, name=metadata.get('name',symbol), asset_type=metadata.get('type','stock'),
                           raw=raw, calendar=sessions, ratios=ratios, basis='QFQ' if ratios is not None else 'RAW',
                           basis_note='QuantDash真实事件前复权，仅投影一次' if ratios is not None else '因子覆盖缺失，明确显示原始价格',
                           asof=cursor['latest'] if cursor else '', source='QuantDash Pro')
        return dict(scope=demand['scope'], client_id=demand['client_id'], requested_symbol=symbol,
                    request_id=demand['request_id'], revision=revision, asof=cursor['latest'] or '' if cursor else '',
                    phase=phase, error=error, status_json=json.dumps(status, ensure_ascii=False, allow_nan=False),
                    payload_json=json.dumps(payload, ensure_ascii=False, separators=(',',':'), allow_nan=False))

    def publish(self, store, demands, sessions, now):
        rows, active, receipts = [], set(), {}
        for demand in demands:
            row = self.view(store, demand, sessions, now)
            active.add(row['scope'])
            receipt = (row['client_id'], row['request_id'], row['revision'])
            if self.published.get(row['scope']) != receipt:
                rows.append(row)
                receipts[row['scope']] = receipt
        for scope in self.published.keys()-active:
            rows.append({k:scope if k=='scope' else '' for k,_ in VIEW_FIELDS})
        self.publisher.publish(rows)
        self.published.update(receipts)
        self.published = {s:r for s,r in self.published.items() if s in active}

    def run(self):
        store = None
        try:
            store = Store(self.folder/'workbench.sqlite3')
            key = self.settings.get('quantdash_token') or os.environ.get('QUANTDASH_TOKEN')
            if not key:
                raise ValueError('QUANTDASH_TOKEN is required in the host environment or private settings')
            self.collector = Collector(key, self.folder/'sdk_cache', stop=self.stop, report=self.report)
            checkpoint = store.meta('source_checkpoint')
            if checkpoint:
                self.collector.restore(checkpoint)
            else:
                self.collector.budgets['minute'].defer(60)  # quota handover from previous MA55 24/min
            sessions = store.meta('sessions', [])
            last_calendar = 0
            while not self.stop.is_set():
                now = str(now_cn())
                try:
                    demands = self.publisher.demands()
                    for demand in sorted(demands, key=lambda d:d['request_ms']):
                        fresh = self.handled_demands.get(demand['scope']) != demand['request_id']
                        pin = demand['pinned'] if fresh and demand['pinned'] != -1 else None
                        store.track(demand['requested_symbol'], pinned=pin, one_minute=demand['period']=='1m')
                        self.handled_demands[demand['scope']] = demand['request_id']
                    with store.conn:
                        for tracked in store.tracked():
                            if not tracked['pinned'] and time()-tracked['visited'] > TTL_SECONDS:
                                store.remove(tracked['symbol'])
                    if store.tracked() and (not sessions or (now_cn().weekday()<5 and now[:10] not in sessions and '09:30'<=now[11:16]<='15:05' and monotonic()-last_calendar>300)):
                        sessions = self.collector.calendar()
                        last_calendar = monotonic()
                        with store.conn:
                            store.set_meta('sessions', sessions)
                    missing = [r['symbol'] for r in store.tracked() if r['factor_day'] != now[:10]]
                    if missing:
                        metadata = {r['symbol']:r for r in self.collector.metadata(missing)}
                        factors = self.collector.factors(missing)
                        for symbol in missing:
                            store.metadata(symbol, metadata.get(symbol, {}), factors.get(symbol), now[:10])
                            self.revisions[symbol] = self.revisions.get(symbol,0)+1
                    with store.conn:
                        for tracked in store.tracked():
                            for period in TABLES:
                                if store.cursor(tracked['symbol'], period):
                                    store.reconcile(tracked['symbol'], period, sessions, now)
                        store.set_meta('source_checkpoint', self.collector.checkpoint())
                    self.report(phase='IDLE', error=None, symbols=store.overview(), calendar_last=sessions[-1] if sessions else None)
                    task = next_task(store, sessions, now)
                    runnable = bool(task)
                    if task:
                        period, start, end, kind, symbols = task
                        budget = self.collector.budgets['daily' if period=='1d' else 'minute']
                        if period != '1d' and not self.quota_ready():
                            self.report(phase='BUDGET_WAIT', error='MA55分钟额度须配置为22或以下，工作台保留2，TSP保留6')
                            runnable = False
                        elif budget.delay():
                            phase = 'RATE_LIMITED' if budget.cooldown > monotonic() and self.state.get('http_status') == 429 else 'QUOTA_WAIT'
                            self.report(phase=phase, wait_seconds=round(budget.delay(),1))
                            runnable = False
                        else:
                            self.report(phase=kind, active_symbols=symbols, active_period=period)
                    self.publish(store, demands, sessions, now)
                    if runnable:
                        before = self.collector.metrics['http_requests']
                        began = perf_counter()
                        try:
                            data = self.collector.fetch(symbols, start, end, period)
                            store.save(data, period, sessions, now, start, end, seconds=perf_counter()-began,
                                       http_requests=self.collector.metrics['http_requests']-before,
                                       kind=kind, checkpoint=self.collector.checkpoint())
                            for symbol in symbols:
                                if symbol in self.collector.symbol_errors:
                                    self.symbol_errors[symbol] = self.collector.symbol_errors[symbol]
                                else:
                                    self.symbol_errors.pop(symbol, None)
                                self.revisions[symbol] = self.revisions.get(symbol,0)+1
                        except (SourceError, ValueError) as exc:
                            for symbol in symbols:
                                self.symbol_errors[symbol] = str(exc)
                                with store.conn:
                                    store.reconcile(symbol, period, sessions, now, attempted=end)
                    self.report(symbols=store.overview())
                except RequestDeferred as exc:
                    delay = max(0, exc.until-monotonic())
                    for budget in self.collector.budgets.values():
                        budget.defer(delay)
                    with store.conn:
                        store.set_meta('source_checkpoint', self.collector.checkpoint())
                except (httpx.HTTPError, ValueError, SourceError) as exc:
                    # External boundary failure is visible; it never becomes an empty successful K response.
                    self.report(phase='CONNECTION_PENDING', error=str(exc))
                self.stop.wait(3)
        except CollectionStopped:
            pass
        except Exception as exc:
            self.report(phase='STOPPED', error=f'{type(exc).__name__}: {exc}')
        finally:
            if store:
                if self.collector:
                    with store.conn:
                        store.set_meta('source_checkpoint', self.collector.checkpoint())
                store.close()
            if self.collector:
                self.collector.close()
            self.publisher.close()

    def close(self):
        self.stop.set()
        self.thread.join(35)
        if not self.thread.is_alive():
            self.lease.close()
