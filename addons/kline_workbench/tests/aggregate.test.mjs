import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregateMinutes, dailyCandles, beijing, epoch } from '../src/aggregate.mjs';
import { movingAverages, eventAnchor } from '../src/indicators.mjs';
import { session } from './fixtures.mjs';
import { withLineBreaks } from '../src/chart.mjs';
const now = '2026-11-08 12:00:00', calendar = ['2026-11-06'];

test('daily MA does not bridge an actually missing exchange session',()=>{
  const row={open:10,high:12,low:9,close:11,volume:3};
  const result=dailyCandles([{...row,date:'2026-11-04'},{...row,date:'2026-11-06'}],now,['2026-11-04','2026-11-05','2026-11-06']);
  assert.equal(result.gaps,1);assert.equal(result.continuous,1);
  assert.equal(movingAverages(result.bars,[2]).get(2).at(-1).value,undefined);
});
test('chart suppresses the outgoing line across an unavailable MA run',()=>{
  const points=[{time:1,value:10},{time:2,value:11},{time:3},{time:4},{time:5,value:12},{time:6,value:13}];
  const draw=withLineBreaks(points,'red');
  assert.equal(draw[0].color,'red');assert.equal(draw[1].color,'transparent');assert.equal(draw[4].color,'red');
  assert.equal(draw[2].value,undefined);assert.equal(points[1].color,undefined);
});
for (const [period, count] of [['1m',240],['5m',48],['15m',16],['30m',8],['60m',4]]) {
  test(`${period}: genuine OHLCV, auction exclusion, separate morning and afternoon`, () => {
    const data = aggregateMinutes([session()], period, now, calendar), n = Number(period.slice(0,-1));
    assert.equal(data.validCount, count); assert.equal(data.auction, 1); assert.equal(data.gaps, 0);
    assert.deepEqual(Object.fromEntries(['open','high','low','close','volume','amount'].map(k => [k,data.bars[0][k]])), { open:100, high:101+n, low:99, close:100+n, volume:10*n, amount:1000*n });
    assert.equal(data.bars[count / 2 - 1].timestamp, '2026-11-06 11:30:00');
    assert.equal(data.bars.at(-1).timestamp, '2026-11-06 15:00:00');
    if (period === '15m') {
      assert.equal(data.bars[0].timestamp, '2026-11-06 09:45:00');
      assert.equal(data.bars[8].timestamp, '2026-11-06 13:15:00');
    }
  });
}
test('completion margin, lunch, end-of-day and Sunday never fabricate current candles', () => {
  for (const [clock, count] of [['09:45:59',0],['09:46:00',1],['12:30:00',8],['13:15:59',8],['13:16:00',9],['15:00:59',15],['15:01:00',16]]) {
    const data = aggregateMinutes([session()], '15m', `2026-11-06 ${clock}`, calendar);
    assert.equal(data.validCount, count, clock);
  }
  assert.equal(aggregateMinutes([session()], '15m', now, calendar).dataAsOf, '2026-11-06 15:00:00');
  assert.equal(beijing(epoch('2026-11-06 09:45:00') * 1000), '2026-11-06 09:45:00');
  assert.equal(beijing('2026-11-06T01:45:00Z'), '2026-11-06 09:45:00');
});
test('missing, null-open and conflicting timestamps are gaps; identical duplicates are harmless', () => {
  for (const corrupt of [s => s.rows.splice(5,1), s => s.rows[5].open=null,
    s => s.rows.push({ ...s.rows[5], close:s.rows[5].close+1 })]) {
    const s = session(); corrupt(s);
    const data = aggregateMinutes([s], '15m', now, calendar);
    assert.equal(data.gaps, 1); assert.equal(data.bars[0].complete, false); assert.equal(data.validCount, 15);
  }
  const s = session(); s.rows.push({ ...s.rows[5] });
  assert.equal(aggregateMinutes([s], '15m', now, calendar).validCount, 16);
});
test('actual missing session inside calendar breaks MA; no overnight or lunch aggregation', () => {
  const data = aggregateMinutes([session('2026-11-03'),session('2026-11-05')], '15m', now, ['2026-11-03','2026-11-04','2026-11-05']);
  assert.equal(data.gaps, 16); assert.equal(data.continuous, 16);
  assert.ok(movingAverages(data.bars,[20]).get(20).every(p => p.value === undefined));
});
test('MA5/55/233 exact values require n complete continuous bars', () => {
  const bars = Array.from({length:320},(_,i)=>({time:i,timestamp:String(i),complete:true,close:i+1}));
  const ma = movingAverages(bars,[5,55,233]);
  assert.equal(ma.get(5)[3].value,undefined); assert.equal(ma.get(5)[4].value,3);
  assert.equal(ma.get(55).at(-1).value,293); assert.equal(ma.get(233).at(-1).value,204);
  bars[315].complete=false;
  assert.equal(movingAverages(bars,[5]).get(5).at(-1).value,undefined);
});
test('20 complete sessions allow 15m MA233, 60m MA233 stays insufficient', () => {
  const dates = []; for(let d=new Date('2026-10-12T00:00:00Z');dates.length<20;d.setUTCDate(d.getUTCDate()+1)) if(d.getUTCDay()>0&&d.getUTCDay()<6) dates.push(d.toISOString().slice(0,10));
  const sessions=dates.map(d=>session(d));
  const fifteen=aggregateMinutes(sessions,'15m',now,dates), hourly=aggregateMinutes(sessions,'60m',now,dates);
  assert.equal(fifteen.validCount,320); assert.ok(Number.isFinite(movingAverages(fifteen.bars,[233]).get(233).at(-1).value));
  assert.equal(hourly.validCount,80); assert.equal(movingAverages(hourly.bars,[233]).get(233).at(-1).value,undefined);
});
test('daily incomplete bars are omitted; anchor never snaps an absent event to another candle', () => {
  const rows=[{date:'2026-11-06',open:100,high:102,low:99,close:101,volume:10}];
  assert.equal(dailyCandles(rows,'2026-11-06 14:00:00').validCount,0);
  assert.equal(dailyCandles(rows,now).validCount,1);
  const bars=aggregateMinutes([session()],'15m',now,calendar).bars;
  assert.equal(eventAnchor(bars,'2026-11-06T13:15:00','15m').index,8);
  assert.match(eventAnchor(bars,'2026-10-30T13:15:00','15m').message,/超出当前可得分钟历史/);
  assert.equal(eventAnchor(bars,'2026-11-06T13:10:00','15m').index,-1);
});
