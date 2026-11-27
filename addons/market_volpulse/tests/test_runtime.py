import asyncio
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import httpx

from backend.calendar import confirmed_sessions
from backend.clock import CN
from backend.collector import save_closed
from backend.projection import build_view
from backend.providers import SYMBOLS, Client, Day, Quote, parse_tencent_days
from backend.schedule import request_failure, session_at
from backend.settings import Settings
from backend.store import Store
from backend.worker import Worker

DAY = '2026-11-27'
PREV = '2026-11-26'
DAYS = ('2026-11-25', PREV, DAY, '2026-11-30')


def at(clock):
    return datetime.fromisoformat(f'{DAY}T{clock}').replace(tzinfo=CN)


def day(symbol, date=DAY, source='tencent', size=240):
    return Day(symbol, date, source, 10, {k: (k + 1) * 100 for k in range(size)}, {})


class History(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite3')

    def test_installed_calendar_holiday_and_friday(self):
        days, preset = confirmed_sessions(at('10:00:00'))
        self.assertIn(DAY, days)
        self.assertIn('2026-11-30', days)
        self.assertNotIn('2026-11-28', days)
        self.assertNotIn('2026-10-05', days)
        self.assertTrue(preset)
        self.assertEqual(session_at(at('11:30:00'), days), (DAY, 'LUNCH'))
        self.assertEqual(session_at(at('13:00:00'), days), (DAY, 'LIVE'))
        self.assertEqual(session_at(at('15:00:00'), days), (DAY, 'POST_CLOSE'))
        self.assertEqual(session_at(at('09:25:00'), days), (DAY, 'AUCTION'))

    def test_no_unverified_days_or_open_minute_commit(self):
        stock = SYMBOLS[0]
        response = [day(stock), day(stock, '2026-11-28')]
        save_closed(self.store, response, at('09:32:18'), DAYS)
        self.assertEqual(set(self.store.load(stock, DAY, 'tencent').amounts), {0, 1})
        self.assertEqual(self.store.available_days(), {DAY})

    def test_thinner_response_cannot_overwrite_a_closed_observation(self):
        stock = SYMBOLS[0]
        self.store.save(day(stock))
        self.assertFalse(self.store.save(Day(stock, DAY, 'tencent', 13, {2: 199}, {})))
        self.assertEqual(self.store.load(stock, DAY, 'tencent').amounts[2], 300)
        with self.assertRaisesRegex(ValueError, '倒退'):
            self.store.save(Day(stock, DAY, 'tencent', 12, {0: 90, 1: 88}, {}))
        final = day(stock)
        self.assertTrue(self.store.save(Day(stock, DAY, 'tencent', 10, final.amounts | {239: 24007}, {})))
        self.assertEqual(self.store.load(stock, DAY, 'tencent').amounts[239], 24007)

    def test_tencent_lightweight_daily_shape(self):
        one = {'date': '20261127', 'data': ['0930 100 10 121', '0931 100 11 125', '1300 100 11 125']}
        result = parse_tencent_days({'data': {'sh000001': {'data': one}}}, SYMBOLS[0])
        self.assertEqual(result[0].auction, 121)
        self.assertEqual(result[0].amounts, {0: 125})

    def test_sina_can_compare_only_after_two_verified_sessions(self):
        for symbol in SYMBOLS:
            self.store.save(day(symbol, PREV))
            self.store.save(day(symbol))
        stock = SYMBOLS[0]
        quotes = {stock: Quote(stock, at('10:00:05'), 3020, 100, 1.1, 'sina')}
        params = dict(day=DAY, previous=PREV, prior_sessions=[PREV, DAYS[0]], now=at('10:00:07'), quotes=quotes, live=True)
        self.store.record_sina_close(PREV, {stock: True})
        one = build_view(self.store, **params, sina_verified=self.store.sina_verified([PREV, DAYS[0]]))
        self.assertEqual(one['indices'][1]['status'], 'DEGRADED')
        self.assertIsNone(one['indices'][1]['snapshot'])
        self.store.record_sina_close(DAYS[0], {stock: True})
        two = build_view(self.store, **params, sina_verified=self.store.sina_verified([PREV, DAYS[0]]))
        self.assertEqual(two['indices'][1]['status'], 'LIVE')
        self.assertEqual(two['indices'][1]['snapshot']['today'], 3020)
        self.assertIsNone(two['indices'][1]['points'][30]['today'])

    def test_eastmoney_independent_pair_and_auction(self):
        stock = '399006.SZ'
        self.store.save(day(stock, PREV, 'eastmoney'))
        self.store.save(day(stock, DAY, 'eastmoney'))
        self.store.save(day(stock, PREV, 'tencent'))
        quotes = {stock: Quote(stock, at('10:00:10'), 3040, 100, 0, 'tencent')}
        params = dict(day=DAY, previous=PREV, prior_sessions=[PREV], quotes=quotes, history_source='auto')
        view = build_view(self.store, **params, now=at('10:00:10'), live=True)
        row = next(row for row in view['indices'] if row['symbol'] == stock)
        self.assertEqual((row['source'], row['status'], row['snapshot']), ('eastmoney', 'DEGRADED', None))
        auction = build_view(self.store, **params, now=at('09:25:15'), phase='AUCTION')
        row = next(row for row in auction['indices'] if row['symbol'] == stock)
        self.assertEqual((row['status'], row['auction']['today'], row['covered_minutes']), ('AUCTION', 10, 0))

    def test_lunch_holding_and_resume_close_index(self):
        for trading_date in (PREV, DAY):
            for stock in SYMBOLS:
                self.store.save(day(stock, trading_date))
        p = dict(day=DAY, previous=PREV, prior_sessions=[PREV], quotes={})
        noon = build_view(self.store, **p, now=at('12:30'), phase='LUNCH')
        resumed = build_view(self.store, **p, now=at('13:01'))
        closed = build_view(self.store, **p, now=at('15:00'))
        self.assertEqual(noon['indices'][0]['covered_minutes'], 120)
        self.assertEqual(resumed['indices'][0]['covered_minutes'], 121)
        self.assertEqual(closed['indices'][0]['metrics']['predicted_amount'], 48000)
        self.assertEqual(closed['indices'][0]['metrics']['prediction_basis'], 'close')
        self.assertEqual(self.store.events(DAY), [])

    def test_retry_after_and_forbidden_use_separate_backoffs(self):
        request = httpx.Request('GET', 'https://market.example/')
        wait = httpx.Response(429, headers={'Retry-After': '110'}, request=request)
        deny = httpx.Response(403, request=request)
        for response, seconds in ((wait, 110), (deny, 900)):
            with self.assertRaises(httpx.HTTPStatusError) as raised:
                response.raise_for_status()
            now = at('10:05')
            message, next_at = request_failure(raised.exception, now, 10)
            self.assertEqual(message, f'HTTP {response.status_code}')
            self.assertEqual(next_at - now, timedelta(seconds=seconds))


class Runtime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.now = at('13:00:07')
        self.worker = Worker(self.folder, clock=lambda: self.now)
        self.worker.store = Store(self.folder / 'state.sqlite3')
        self.worker.publisher = AsyncMock()
        self.worker.publisher.command.return_value = None
        self.worker.sessions = DAYS
        self.worker.day, self.worker.prior = DAY, (PREV, DAYS[0])
        self.worker.phase = 'LIVE'

    async def test_hot_settings_and_replayed_revision(self):
        settings = Settings(refresh_seconds=3, window=10, notifications=True)
        command = dict(kind='settings', request_id='2' * 32, sent_at=int(self.now.timestamp() * 1000),
                       revision=0, values=settings.public())
        receipt = await self.worker._apply_command(command, self.now)
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['revision'], 1)
        self.assertEqual(self.worker.next_quote, self.now)
        self.assertEqual(Store(self.folder / 'state.sqlite3').load_settings(), (settings, 1, '2' * 32))
        self.assertIsNone(await self.worker._apply_command(command, self.now))
        conflict = await self.worker._apply_command(command | {'request_id': '3' * 32}, self.now)
        self.assertFalse(conflict['ok'])
        self.assertEqual(self.worker.settings, settings)

    async def test_publish_failure_retries_and_tracks_previous_success(self):
        self.worker.dirty = True
        self.worker._status(publish_ok_at=at('12:55').isoformat())
        self.worker.publisher.side_effect = httpx.ConnectError('unavailable')
        await self.worker._publish(self.now)
        self.assertTrue(self.worker.dirty)
        self.assertEqual(self.worker.status['publish_error'], '连接失败')
        self.assertEqual(self.worker.status['publish_ok_at'], at('12:55').isoformat())
        self.worker.publisher.side_effect = None
        self.worker.publisher.last_bytes = 123
        await self.worker._publish(self.now + timedelta(seconds=20))
        self.assertFalse(self.worker.dirty)
        self.assertIsNone(self.worker.status['publish_error'])

    async def test_settings_receipt_waits_for_the_matching_view(self):
        self.worker.next_command = self.now
        self.worker.publisher.command.return_value = dict(
            kind='settings', request_id='4' * 32, sent_at=int(self.now.timestamp() * 1000),
            revision=0, values=Settings(window=10).public(),
        )
        await self.worker._commands(self.now)
        self.worker.publisher.put.assert_not_awaited()
        await self.worker._publish(self.now)
        await self.worker._commands(self.now + timedelta(seconds=4))
        self.worker.publisher.put.assert_awaited_once()
        self.assertEqual(self.worker.publisher.call_args.args[0]['window'], 10)
        self.assertEqual(self.worker.publisher.put.call_args.args[1]['revision'], 1)

    async def test_duplicate_owner_releases_lease_on_stop(self):
        path = self.folder / 'bridge.json'
        path.write_text(json.dumps({'api_base': 'http://127.0.0.1:3018', 'api_token': 'test'}))
        entered = asyncio.Event()

        async def hold():
            entered.set()
            return 60

        self.worker.tick = hold
        self.worker.start()
        task = self.worker.task
        self.worker.start()
        self.assertIs(self.worker.task, task)
        await asyncio.wait_for(entered.wait(), 4)
        second = Worker(self.folder)
        second.start()
        await asyncio.wait_for(second.task, 4)
        self.assertEqual(second.status['state'], 'OTHER_OWNER')
        await self.worker.stop()
        entered.clear()
        second.tick = hold
        second.start()
        await asyncio.wait_for(entered.wait(), 4)
        await second.stop()

    async def test_snapshot_rate_limit_and_switch_back(self):
        class FakeMarket:
            async def quotes(inner_self, source):
                if source == 'tencent' and self.now == at('13:00:07'):
                    req = httpx.Request('GET', 'https://market.example/')
                    httpx.Response(429, request=req, headers={'Retry-After': '80'}).raise_for_status()
                return {s: Quote(s, self.now, 12110, 100.0, 0, source) for s in SYMBOLS}

        self.worker.source = FakeMarket()
        await self.worker._quote(self.now)
        self.assertEqual(self.worker.quote_due['tencent'], self.now + timedelta(seconds=80))
        self.now += timedelta(seconds=5)
        await self.worker._quote(self.now)
        self.assertEqual(self.worker.last_quote_source, 'sina')
        self.now += timedelta(seconds=80)
        await self.worker._quote(self.now)
        self.assertEqual(self.worker.last_quote_source, 'tencent')

    async def test_client_checks_http_status_before_parsing(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(403, text='forbidden')

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with self.assertRaises(httpx.HTTPStatusError):
                await Client(http).quotes('sina')
        self.assertEqual(seen[0].headers['referer'], 'https://finance.sina.com.cn/')
        self.assertEqual(len(seen), 1)
