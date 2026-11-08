"""Small temporary stores and HTTP-hook cases; synthetic data is not live evidence."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest

import httpx

from addons.kline_workbench.backend.collector import Collector
from addons.kline_workbench.backend.market import grid, window, projection, as_ms, normalize
from addons.kline_workbench.backend.publisher import Publisher, configs
from addons.kline_workbench.backend.request_budget import RequestDeferred
from addons.kline_workbench.backend.service import next_task, Worker
from addons.kline_workbench.backend.store import Store


def sessions(n=80):
    day=date(2026,7,1); result=[]
    while len(result)<n:
        if day.weekday()<5:
            result.append(str(day))
        day+=timedelta(days=1)
    return result


def bars(points):
    return {t:dict(open=10,high=12,low=9,close=11,volume=3,amount=3300) for t in points}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.path=Path(self.temp.name)/'workbench.sqlite3'; self.store=Store(self.path)
        self.days=sessions(); self.now=self.days[74]+' 16:00:00'; self.symbol='600519.SH'
        self.store.track(self.symbol,pinned=True,one_minute=False)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def save(self, period, points, end=None):
        self.store.save({self.symbol:bars(points)},period,self.days,self.now,points[0],end or points[-1],seconds=.01,http_requests=1,kind='COLD',checkpoint={})

    def test_75_days_roll_upsert_restart_no_refetch_and_20_day_lazy(self):
        _, points=window(self.days,'5m',self.now)
        self.assertEqual(len(points),3600);self.save('5m',points)
        self.assertEqual(self.store.cursor(self.symbol,'5m')['gaps'],0)
        # No 1m job until explicitly requested. Daily facts remain a separate raw source.
        self.assertNotEqual(next_task(self.store,self.days,self.now)[0],'1m')
        self.store.close();self.store=Store(self.path)
        self.assertNotEqual(next_task(self.store,self.days,self.now)[0],'5m')
        self.now=self.days[75]+' 09:36:00'; point=self.days[75]+' 09:35:00'
        self.save('5m',[point]);self.save('5m',[point])
        rows=self.store.rows(self.symbol,'5m')
        self.assertEqual(len({r['end_time'][:10] for r in rows}),75)
        self.assertEqual(rows[0]['end_time'][:10],self.days[1]);self.assertEqual(len(rows),74*48+1)
        self.store.track(self.symbol,pinned=None,one_minute=True)
        self.assertEqual(self.store.tracked()[0]['pinned'],1)
        one_days,one_points=window(self.days,'1m',self.days[75]+' 16:00:00')
        self.assertEqual((len(one_days),len(one_points)),(20,4800))

    def test_gap_repair_stops_before_stored_bar_and_new_completed_bar_first(self):
        _,points=window(self.days,'5m',self.now)
        self.save('5m',points[:7]+points[8:])
        cursor=self.store.cursor(self.symbol,'5m')
        self.assertEqual((cursor['gaps'],cursor['gap_start'],cursor['gap_end']),(1,points[7],points[7]))
        with self.store.conn:
            self.store.conn.execute('UPDATE symbol_cursor SET retry_at=0')
        # Cold daily may precede an old gap; a new 5m completion precedes both.
        new=self.days[75]+' 09:36:00'
        task=next_task(self.store,self.days,new)
        self.assertEqual((task[0],task[3]),('5m','INCREMENT'))
        self.assertEqual(task[1],self.days[75]+' 09:35:00')

    def test_ttl_lru_bounded_and_pinned_survives(self):
        for i in range(50):
            self.store.track(f'{i:06}.SZ',pinned=False,one_minute=False,now=100+i)
        self.assertEqual(len(self.store.tracked()),50)
        self.assertIn(self.symbol,[r['symbol'] for r in self.store.tracked()])
        self.store.track('999999.SZ',pinned=False,one_minute=False,now=8*86400)
        self.assertEqual({r['symbol'] for r in self.store.tracked()},{self.symbol,'999999.SZ'})

    def test_calendar_completion_and_factor_missing_are_explicit(self):
        friday=self.days[74]
        _,points=window(self.days,'5m',friday+' 09:35:30')
        self.assertNotIn(friday+' 09:35:00',points)
        self.assertEqual(len(points),74*48)
        self.assertIsNone(projection(None,[friday],friday))
        self.assertEqual(projection([], [friday],friday),{friday:1})
        self.assertEqual(projection([dict(day=friday,ex_factor=2)],['2026-07-01',friday],friday),{'2026-07-01':.5,friday:1})


class BoundaryTests(unittest.TestCase):
    def test_bad_symbol_payload_does_not_discard_healthy_batch_member(self):
        at='2026-11-06 09:35:00'
        good=dict(timestamp=[as_ms(at)],open=[10],high=[12],low=[9],close=[11],volume=[3])
        bad={k:v for k,v in good.items() if k!='close'}
        class QD:
            def __init__(self):
                self._client=SimpleNamespace(_client=httpx.Client())
                self.klines=SimpleNamespace(batch=lambda *a,**kw:{'600519.SH':good,'000001.SZ':bad})
            def close(self): self._client._client.close()
        collector=Collector('', '.', qd=QD())
        rows=collector.fetch(['600519.SH','000001.SZ'],at,at)
        self.assertEqual(list(rows['600519.SH']),[at]);self.assertEqual(rows['000001.SZ'],{})
        self.assertEqual(set(collector.symbol_errors),{'000001.SZ'});collector.close()

    def test_quota_failure_is_published_with_new_revision_and_retained_raw_history(self):
        with TemporaryDirectory() as folder:
            worker=Worker(SimpleNamespace(data_dir=folder),{'api_base':'http://local'})
            store=Store(Path(folder)/'test.sqlite3')
            try:
                store.track('600519.SH',pinned=None,one_minute=False)
                demand=dict(scope='0',client_id='a',request_id='1',requested_symbol='600519.SH',period='15m')
                first=worker.view(store,demand,[],'2026-11-08 12:00:00')
                worker.report(phase='BUDGET_WAIT',error='MA5522+工作台2+TSP6')
                second=worker.view(store,demand,[],'2026-11-08 12:00:00')
                self.assertNotEqual(first['revision'],second['revision'])
                import json
                self.assertEqual(json.loads(second['status_json'])['quota'],'BUDGET_WAIT')
                self.assertIn('MA55',second['error'])
            finally:
                store.close();worker.publisher.close()

    def test_real_http_hooks_count_both_minute_types_and_429_defer(self):
        class FakeQD:
            def __init__(self):
                self._client=type('Inner',(),{'_client':httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429,json={'error':{'retry_after_ms':4500}})))})()
            def close(self): self._client._client.close()
        qd=FakeQD();collector=Collector('', '.', qd=qd,stop=Event())
        with self.assertRaises(RequestDeferred):
            collector.request(lambda:qd._client._client.get('https://source.test/klines/batch?period=1m'))
        self.assertEqual(collector.metrics['minute_requests'],1)
        self.assertEqual(collector.metrics['http_429_count'],1)
        self.assertGreater(collector.budgets['minute'].delay(),4.5)
        collector.close()

    def test_bridge_uses_official_routes_and_fixed_non_symbol_keys(self):
        requests=[]
        def handle(request):
            requests.append((request.method,request.url.path))
            return httpx.Response(200,json={'items':[]} if request.method=='GET' else {'ok':True})
        publisher=Publisher('http://local',transport=httpx.MockTransport(handle));publisher.prepare();publisher.close()
        self.assertEqual(requests[0],('GET','/api/ext-data'))
        self.assertEqual(len(requests),3)
        for config in configs():
            self.assertTrue(config['market_level']);self.assertEqual(config['fields'][0]['name'],'scope')
            self.assertNotIn('symbol',[f['name'] for f in config['fields']])

    def test_source_invalid_ohlc_is_a_gap_not_fabricated(self):
        t='2026-11-06 09:35:00'
        data=dict(timestamp=[as_ms(t)],open=[10],high=[8],low=[9],close=[11],volume=[1])
        self.assertEqual(normalize(data,t,t,'5m'),{})

    def test_one_minute_auction_is_excluded_without_discarding_real_minutes(self):
        start='2026-11-05 09:31:00';end='2026-11-06 09:31:00'
        data=dict(timestamp=[as_ms('2026-11-06 09:30:00'),as_ms(end)],
                  open=[10,10],high=[12,12],low=[9,9],close=[11,11],volume=[2,3])
        self.assertEqual(list(normalize(data,start,end,'1m')),[end])


if __name__=='__main__':
    unittest.main()
