"""Single owner for the bounded quote, history and Ext Data exchanges."""
import asyncio
import json
import logging
import os
import re
import sqlite3
from collections import Counter, deque
from datetime import datetime, timedelta

import httpx

from .bridge import BRIDGE_FILE, local_url
from .calendar import confirmed_sessions
from .clock import CN, position
from .collector import record_alerts, save_closed
from .projection import build_view, cutoff_for, prepare_view
from .providers import SYMBOLS, Client
from .publisher import RECEIPT_TABLE, Publisher
from .schedule import CLOSE, next_open, request_failure, session_at
from .settings import Settings
from .store import Store

LOG = logging.getLogger(__name__)
COMMAND_INTERVAL = 4
MINUTE_SETTLE = 6


class Worker:
    def __init__(self, folder, *, clock=lambda: datetime.now(CN), calendar=confirmed_sessions):
        self.folder = folder
        self.clock = clock
        self.load_calendar = calendar
        self.task = None
        self.status = dict(state='STARTING', worker_pid=os.getpid())
        self.settings = Settings()
        self.revision = 0
        self.published_revision = -1
        self.last_request = None
        self.receipt = None
        self.sessions = ()
        self.prior = ()
        self.verified_sina = set()
        self.day = self.phase = self.calendar_day = None
        self.calendar_version = None
        self.requests = Counter()
        self.quote_data = {}
        self.quote_errors = {}
        self.quote_due = {}
        self.quote_failures = Counter()
        self.last_quote_source = None
        self.history_errors = {}
        self.history_due = {}
        self.history_key = None
        self.last_final_attempt = None
        self.history_jobs = deque()
        self.close_check_day = None
        self.next_close_check = None
        self.baselines = None
        self.baseline_key = None
        self.dirty = True

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self.run(), name='market.volpulse')

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        bridge = self.folder / BRIDGE_FILE
        if not bridge.is_file():
            self._status(state='WAITING_FOR_SETUP', reason='请通过 TSP 管理会话准备扩展表和发布凭据')
            return
        lease = None
        try:
            config = json.loads(bridge.read_text(encoding='utf-8'))
            url = local_url(config['api_base'])
            lease = sqlite3.connect(self.folder / 'owner.sqlite3', timeout=0)
            try:
                lease.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
                self._status(state='OTHER_OWNER', reason='本机量能采集已由另一个进程持有')
                return
            self.store = Store(self.folder / 'state.sqlite3')
            self.settings, self.revision, self.last_request = self.store.load_settings()
            now = self.clock()
            self.next_quote = self.next_history = self.next_command = self.next_publish = self.next_calendar = now
            async with (httpx.AsyncClient(timeout=3, headers={'User-Agent': 'TSP Market VolPulse/0.1.0'}) as source_http,
                        httpx.AsyncClient(base_url=url, timeout=5, trust_env=False,
                                          headers={'Authorization': 'Bearer ' + config['api_token']}) as host_http):
                self.source, self.publisher = Client(source_http), Publisher(host_http)
                while True:
                    try:
                        delay = await self.tick()
                    except sqlite3.Error as exc:
                        self._status(state='DEGRADED', reason='插件 SQLite 读写失败')
                        LOG.warning('VolPulse SQLite unavailable: %s', type(exc).__name__)
                        delay = 10
                    await asyncio.sleep(delay)
        except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            LOG.warning('VolPulse startup failed: %s', type(exc).__name__)
            self._status(state='STARTUP_ERROR', reason='插件本地配置或数据库打不开')
        finally:
            if lease is not None:
                lease.close()
        if self.status['state'] == 'RUNNING':
            self._status(state='STOPPED')

    def _status(self, **values):
        self.status = {**self.status, **values}

    async def _calendar(self, now):
        today = now.date().isoformat()
        if self.calendar_day == today or now < self.next_calendar:
            return
        try:
            self.sessions, self.calendar_version = await asyncio.to_thread(self.load_calendar, now)
            if not self.sessions:
                raise ValueError('日历没有可用交易日')
            await asyncio.to_thread(self.store.retain, [day for day in self.sessions if day <= today])
        except ValueError as exc:
            self.sessions, self.calendar_version = (), None
            self.next_calendar = now + timedelta(minutes=5)
            self._status(calendar_error=str(exc))
        else:
            self.calendar_day = today
            self._status(calendar_error=None)
        self.dirty = True

    async def tick(self):
        now = self.clock()
        await self._calendar(now)
        day, phase = session_at(now, self.sessions) if self.sessions else (None, 'CALENDAR_UNVERIFIED')
        if day != self.day:
            self.prior = tuple(d for d in reversed(self.sessions) if day and d < day)
            self.verified_sina = self.store.sina_verified(self.prior)
            self.quote_data = {}
            self._status(quote_ok_at=None, history_ok_at=None)
        if (day, phase) != (self.day, self.phase):
            self.day, self.phase = day, phase
            self.history_jobs.clear()
            self.history_key = None
            self.next_quote = now
            self.dirty = True
        self._status(state='RUNNING', reason=None, phase=phase, trade_date=day, calendar=self.calendar_version)
        await self._commands(now)
        now = self.clock()
        if self.phase == 'LIVE' and now >= self.next_quote:
            await self._quote(now)
        await self._history(self.clock())
        now = self.clock()
        if self.phase == 'POST_CLOSE' and now.time() >= CLOSE.replace(second=20):
            await self._confirm_sina(now)
        now = self.clock()
        if self.phase == 'LIVE' and self.next_quote > now:
            next_market = self.next_quote
        else:
            next_market = next_open(now, self.sessions)
        if self.history_jobs and (next_market is None or self.next_history < next_market):
            next_market = self.next_history
        self._status(quote_requests=dict(self.requests), next_market_request=next_market.isoformat() if next_market else None,
                     quote_source=self.last_quote_source, quote_errors=dict(self.quote_errors),
                     history_errors=dict(self.history_errors), settings_revision=self.revision)
        if self.dirty and now >= self.next_publish and (self.phase == 'LIVE' or not self.history_jobs):
            await self._publish(now)
        return .45 if self.history_jobs or self.phase == 'LIVE' else 1

    async def _market(self, kind, source, now, *args, **kwargs):
        self.requests[f'{source}:{kind}'] += 1
        # Enforce a total deadline as well as read/connect timeouts.
        try:
            async with asyncio.timeout(4):
                return await (self.source.quotes(source) if kind == 'quote'
                              else self.source.days(*args, source=source, **kwargs))
        except TimeoutError:
            raise httpx.TimeoutException('market request deadline exceeded') from None

    async def _quote(self, now):
        interval = self.settings.refresh_seconds
        source = next((s for s in ('tencent', 'sina') if self.quote_due.get(s, now) <= now), None)
        if source is None:
            self.next_quote = min(self.quote_due.values())
            return
        try:
            quotes = await self._market('quote', source, now)
            received = self.clock()
            age = max(12, interval * 2 + 2)
            good = {k: q for k, q in quotes.items() if q.stamp.date().isoformat() == self.day
                    and -1 <= (received - q.stamp).total_seconds() <= age and q.amount > 0 and position(q.stamp)}
            if not {'000001.SH', '399106.SZ'} <= good.keys():
                if good:
                    self.quote_data = good
                    self.last_quote_source = source
                raise ValueError('both exchanges require fresh snapshots')
        except (httpx.HTTPError, ValueError) as exc:
            self.quote_failures[source] += 1
            backoff = min(120, interval * 2 ** min(self.quote_failures[source], 5))
            reason, due = request_failure(exc, now, backoff)
            self.quote_due[source] = due
            self.quote_errors[source] = reason
        else:
            self.quote_data, self.last_quote_source = good, source
            self.quote_due.pop(source, None)
            self.quote_errors.pop(source, None)
            self.quote_failures[source] = 0
            self._status(quote_ok_at=received.isoformat())
        self.next_quote = now + timedelta(seconds=interval)
        self.dirty = True

    async def _history(self, now):
        if not self.day:
            return
        cutoff = cutoff_for(self.day, now - timedelta(seconds=MINUTE_SETTLE))
        if self.phase in ('LUNCH', 'POST_CLOSE') and cutoff < (119 if self.phase == 'LUNCH' else 239):
            return
        key = (self.day, self.phase, cutoff)
        interval = 25 if self.phase == 'AUCTION' else 90 if self.phase == 'LUNCH' else 300
        due_for_repair = (self.phase != 'LIVE' and self.last_final_attempt is not None
                          and (now - self.last_final_attempt).total_seconds() >= interval)
        if not self.history_jobs and (key != self.history_key or due_for_repair):
            self.history_key = key
            self.last_final_attempt = now
            for symbol in SYMBOLS:
                current = self.store.load(symbol, self.day, 'tencent')
                prior = self.store.load(symbol, self.prior[0], 'tencent') if self.prior else None
                missing = len(current.amounts) < cutoff + 1 or current.auction is None
                deep = prior is not None and len(prior.amounts) < 240
                if missing or deep:
                    # Missing yesterday can wait; use the one-day feed between repair rounds.
                    previous_pull = self.history_due.get(('deep', symbol), now)
                    deep = (deep or self.phase in ('CLOSED_DAY', 'PREOPEN')) and now >= previous_pull
                    self.history_jobs.append((symbol, deep, 'tencent'))
        if not self.history_jobs or now < self.next_history:
            return
        symbol, deep, source = self.history_jobs.popleft()
        if now < self.history_due.get(source, now):
            if source == 'tencent':
                self.history_jobs.append((symbol, False, 'eastmoney'))
            return
        if deep:
            self.history_due['deep', symbol] = now + timedelta(minutes=10)
        self.next_history = now + timedelta(milliseconds=550)
        try:
            records = await self._market('history', source, now, symbol, deep=deep)
            # This is the request start cutoff, not the later response time.
            changed = await asyncio.to_thread(save_closed, self.store, records,
                                              now - timedelta(seconds=MINUTE_SETTLE), self.sessions)
            current = self.store.load(symbol, self.day, source)
            if len(current.amounts) < cutoff + 1 or current.auction is None:
                self.history_errors[symbol] = f'{source} 分时尚未齐全'
                # Don't hammer trends2 for a bar Tencent has just started to deliver.
                if source == 'tencent' and cutoff >= 0 and len(current.amounts) < cutoff:
                    self.history_jobs.append((symbol, False, 'eastmoney'))
            else:
                self.history_errors.pop(symbol, None)
                self._status(history_ok_at=self.clock().isoformat())
            if changed:
                self.baselines = None
            self.dirty = True
        except (httpx.HTTPError, ValueError) as exc:
            reason, due = request_failure(exc, now, 25)
            if isinstance(exc, httpx.HTTPError):
                self.history_due[source] = due
            self.history_errors[symbol] = f'{source} {reason}'
            if source == 'tencent':
                self.history_jobs.append((symbol, False, 'eastmoney'))
            self.dirty = True

    async def _confirm_sina(self, now):
        if self.close_check_day == self.day or self.history_jobs or (
            self.next_close_check is not None and now < self.next_close_check
        ):
            return
        if any(now < due for due in self.quote_due.values()):
            return
        if self.store.has_sina_check(self.day):
            self.close_check_day = self.day
            self._status(sina_check='已保存同日核对')
            return
        closes = {s: self.store.load(s, self.day, 'tencent').amounts.get(239) for s in SYMBOLS}
        if any(value is None for value in closes.values()):
            return
        try:
            primary = await self._market('quote', 'tencent', now)
            backup = await self._market('quote', 'sina', now)
        except (httpx.HTTPError, ValueError) as exc:
            reason, self.next_close_check = request_failure(exc, now, 300)
            self._status(sina_check=reason)
            return
        comparisons = {}
        for symbol, close in closes.items():
            a, b = primary.get(symbol), backup.get(symbol)
            comparisons[symbol] = bool(
                a and b and all(q.stamp.date().isoformat() == self.day and q.stamp.time() >= CLOSE for q in (a, b))
                and abs(a.amount - b.amount) <= 1 and abs(a.amount - close) <= max(1, close * 1e-6)
            )
        self.store.record_sina_close(self.day, comparisons)
        self.close_check_day = self.day
        self._status(sina_check=comparisons)

    async def _view(self, day, prior, now, phase, *, market=False):
        if not day or not prior:
            return dict(schema_version=1, mode='HISTORICAL', phase=phase, generated_at=now.isoformat(),
                        indices=[], events=[])
        cutoff = cutoff_for(day, now)
        params = dict(day=day, previous=prior[0], prior_sessions=prior, history_source='auto')
        key = (day, cutoff, self.settings.window)
        if market:
            if self.baselines is None or key != self.baseline_key:
                self.baselines = await asyncio.to_thread(prepare_view, self.store, **params, cutoff=cutoff,
                                                          window=self.settings.window)
                self.baseline_key = key
            baselines = self.baselines
        else:
            baselines = await asyncio.to_thread(prepare_view, self.store, **params, cutoff=cutoff,
                                                window=self.settings.window)
        return build_view(self.store, **params, now=now, phase=phase, config=self.settings,
                          quotes=self.quote_data if phase == 'LIVE' else {},
                          sina_verified=self.verified_sina if phase == 'LIVE' else (),
                          max_age=max(12, self.settings.refresh_seconds * 2 + 2), baselines=baselines)

    async def _publish(self, now):
        view = await self._view(self.day, self.prior, now, self.phase, market=True)
        record_alerts(self.store, view, self.settings)
        coverage = {r['symbol']: dict(source=r['source'], minutes=r['covered_minutes'], status=r['status'])
                    for r in view['indices']}
        self._status(coverage=coverage)
        history = sorted((self.store.available_days() & set(self.sessions)) - {self.day}, reverse=True)[:30]
        view.update(runtime=self.status, settings=self.settings.public(), settings_revision=self.revision, dates=history)
        try:
            await self.publisher(view)
        except (httpx.HTTPError, ValueError) as exc:
            reason, self.next_publish = request_failure(exc, now, 15)
            self._status(publish_error=reason)
        else:
            self._status(publish_ok_at=self.clock().isoformat(), publish_error=None, view_bytes=self.publisher.last_bytes)
            self.published_revision = self.revision
            self.next_publish = now + timedelta(seconds=self.settings.refresh_seconds if self.phase == 'LIVE' else 1)
            self.dirty = False

    async def _commands(self, now):
        if now < self.next_command:
            return
        self.next_command = now + timedelta(seconds=COMMAND_INTERVAL)
        try:
            if self.receipt is None:
                command = await self.publisher.command()
                if command is not None:
                    self.receipt = await self._apply_command(command, now)
            if self.receipt is not None:
                if self.receipt.get('revision', -1) > self.published_revision:
                    return
                await self.publisher.put(RECEIPT_TABLE, self.receipt)
                self.receipt = None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            reason, self.next_command = request_failure(exc, now, 10)
            self._status(command_error=reason)
        else:
            self._status(command_error=None)

    async def _apply_command(self, command, now):
        rid, sent = command.get('request_id'), command.get('sent_at')
        if (not isinstance(rid, str) or not re.fullmatch(r'[0-9a-f]{32}', rid)
                or type(sent) is not int or not -3 <= now.timestamp() - sent / 1000 <= 45):
            return None
        if rid == getattr(self, 'handled_id', None):
            return None
        receipt = dict(request_id=rid, ok=True)
        self.handled_id = rid
        try:
            if command.get('kind') == 'settings':
                if rid != self.last_request:
                    if type(command.get('revision')) is not int or command['revision'] != self.revision:
                        raise ValueError('设置已被另一个窗口更新，请刷新后重试')
                    settings = Settings.from_command(command.get('values'))
                    self.store.save_settings(settings, self.revision + 1, rid)
                    self.settings, self.revision, self.last_request = settings, self.revision + 1, rid
                    self.next_quote = self.next_publish = now
                    self.dirty = True
                receipt.update(settings=self.settings.public(), revision=self.revision)
            elif command.get('kind') == 'history':
                day = command.get('day')
                if (not isinstance(day, str) or not self.day or day >= self.day
                        or day not in self.sessions or day not in self.store.available_days()):
                    raise ValueError('所选交易日未保留分时')
                prior = tuple(d for d in reversed(self.sessions) if d < day)
                receipt['view'] = await self._view(day, prior, now, 'HISTORICAL')
            else:
                raise ValueError('未知页面请求')
        except ValueError as exc:
            receipt.update(ok=False, error=str(exc))
        return receipt
