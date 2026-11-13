"""Virtual Beijing clock, actual-HTTP rolling quotas and serial worker lanes.

Times are seconds after 09:00 Beijing on Config.day. One Request is exactly one
HTTP call, not one SDK convenience call. No network, storage or strategy writes.
"""

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, replace
from datetime import date
import heapq
import math
import random


def at(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    return (hour - 9) * 3600 + minute * 60


def session_points(period: int, *, day="2026-11-09", holiday=False) -> list[int]:
    if holiday or date.fromisoformat(day).weekday() >= 5:
        return []
    return [start + minute * 60 for start in (at("09:30"), at("13:00"))
            for minute in range(period, 121, period)]


def next_session(now: float, *, quotes=False) -> float | None:
    for start, end in ((at("09:15") if quotes else at("09:30"), at("11:30")),
                       (at("13:00"), at("15:00"))):
        if now <= end:
            return max(start, now)
    return None


def latest_closed(now: float, period=1) -> int:
    return max((p for p in session_points(period) if p <= now), default=at("09:30"))


class RollingWindow:
    """Half-open (t-60,t] accounting: idle time never earns future credits."""

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("rolling limit must be positive")
        self.limit, self.sent, self.peak = limit, deque(), 0

    def _expire(self, now):
        # Compare using the same addition as ready(); subtracting 60 can round
        # differently at fractional completion times and admit a 25th request.
        while self.sent and self.sent[0] + 60 <= now:
            self.sent.popleft()

    def ready(self, now):
        self._expire(now)
        return max(now, self.sent[-self.limit] + 60) if len(self.sent) >= self.limit else now

    def charge(self, now):
        self._expire(now)
        self.sent.append(now)
        self.peak = max(self.peak, len(self.sent))


@dataclass(frozen=True)
class Config:
    universe: int = 5891
    etfs: int = 1500  # explicit load assumption, not an observed ETF count
    metadata_symbols: int = 8275  # observed candidate master size
    day: str = "2026-11-09"
    holiday: bool = False
    seed: int = 11
    stop_s: float = at("17:30")
    host_rpm: int = 12
    reserve: int = 6
    market_rpm: int = 24
    policy: str = "independent"  # current / proposed static / proposed broker
    minute_budget: int = 24
    static_host_minute: int = 12
    vendor_scope: str = "minute_shared"  # or unverified account-wide 30 HTTP/min
    independent_periods: bool = False  # hypothetical only; never assumed verified
    full_interval_minutes: int = 2
    post_minute_bars: int = 1200  # explicit five-session catch-up stress, not live settings
    quote_seconds: int = 30
    ma_period: int = 5
    focus: int = 10
    depth: int = 1
    focus_seconds: int = 120
    depth_seconds: int = 120
    latency_p50_s: float = 0.5
    latency_p95_s: float = 2.0
    bandwidth_MB_s: float = 20
    parse_rows_s: float = 200000
    write_rows_s: float = 100000
    transaction_ms: float = 1
    history_rows_s: float = 5000
    consumer_s: float = 10
    enriched_s: float = 120
    wire_row_bytes: int = 120
    disk_row_bytes: int = 400  # estimated JSON bar + MA/provenance, not file size
    live_rewrite_bars: int = 465  # 144+48+24+12+6+3+128 bounded-window SQL upserts
    warmup_minutes: int = 0
    fault: str = ""
    outage_minutes: int = 10


@dataclass
class Request:
    ready_s: float
    client: str
    endpoint: str
    stream: str
    batch: int
    symbols: int
    period: str = ""
    target_s: float = 0
    bars: int = 1
    priority: int = 3
    lane: str = ""
    round_id: str = ""
    stage: str = ""
    deadline_s: float | None = None
    nonblocking: bool = False
    retry: int = 0


@dataclass(frozen=True)
class RoundSchedule:
    stream: str
    templates: tuple[Request, ...]
    interval_s: float
    first_s: float
    finish_relative: bool = False
    quotes: bool = False


class Simulator:
    def __init__(self, config: Config):
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.events, self.ready = [], defaultdict(list)
        self.serial = 0
        self.local, self.vendor, self.broker = {}, {}, {}
        self.attempts = {}
        self.counts = Counter()
        self.busy = set()
        self.rounds, self.failed_rounds = {}, set()
        self.observations = defaultdict(list)
        self.round_durations = defaultdict(list)
        self.stage_remaining = Counter()
        self.finished_stages = []
        self.pipeline = dict(status="PENDING", failed_stage=None, finished_s=None)
        self.cooldown = 0
        self.injected = False
        self.last_http = None
        self.retry_delays = []
        self.queue_peak = 0
        self.first_overdue = None
        self.missed_slots = 0
        self.history_ready = {}
        self.first_missed_cadence = None

    def _event(self, when, kind, payload):
        self.serial += 1
        heapq.heappush(self.events, (when, self.serial, kind, payload))

    def add(self, requests):
        for request in requests:
            job = replace(request)
            self._event(job.ready_s, "release", job)
            self.counts["offered"] += job.endpoint != "compute"
            if job.round_id:
                state = self.rounds.setdefault(job.round_id, dict(
                    left=0, start=job.ready_s, stream=job.stream, schedule=None))
                state["left"] += 1
            if job.stage:
                self.stage_remaining[job.stage] += 1

    def add_schedule(self, schedule: RoundSchedule):
        self._event(schedule.first_s, "round", schedule)

    @staticmethod
    def _window(mapping, key, limit):
        return mapping.setdefault(key, RollingWindow(limit))

    def _pool(self, job):
        if job.endpoint == "minute_batch":
            return "minute_" + job.period if self.cfg.independent_periods else "minute_shared"
        return job.endpoint

    def _vendor_windows(self, job):
        limits = dict(minute_batch=30, minute_single=60, quote_pool=60,
                      quote_symbols=120, depth=60, daily=60, factors=60, metadata=60)
        result = [(self._pool(job), limits[job.endpoint])]
        if self.cfg.vendor_scope == "account":
            result.append(("account_all_http", 30))
        return result

    def _local_windows(self, job):
        cfg = self.cfg
        if job.client == "host":
            windows = [("host_all", cfg.host_rpm)]
            if job.stream != "quotes":
                windows.append(("host_background", cfg.host_rpm - min(cfg.reserve, cfg.host_rpm - 1)))
            if cfg.policy == "static" and job.endpoint == "minute_batch":
                windows.append(("host_minute_static", cfg.static_host_minute))
        else:
            limit = cfg.market_rpm if job.endpoint == "minute_batch" else 24
            if cfg.policy == "static" and job.endpoint == "minute_batch":
                limit = cfg.minute_budget - cfg.static_host_minute
            windows = [("marketdata_" + job.endpoint, limit)]
        return [self._window(self.local, name, limit) for name, limit in windows]

    def _gates(self, job):
        if job.endpoint == "compute":
            return []
        gates = self._local_windows(job)
        if self.cfg.policy == "broker":
            for name, limit in self._vendor_windows(job):
                budget = self.cfg.minute_budget if name.startswith("minute_") else limit
                if name == "account_all_http":
                    budget = 24
                gates.append(self._window(self.broker, name, budget))
            if self.cfg.vendor_scope == "account" and job.stream != "quotes":
                # Cross-client background bursts otherwise consume the next
                # snapshot's quota, as the account-scope replay demonstrates.
                gates.append(self._window(self.broker, "account_background", 18))
        return gates

    def _duration(self, job):
        cfg = self.cfg
        if job.endpoint == "compute":
            return cfg.enriched_s
        sigma = math.log(cfg.latency_p95_s / cfg.latency_p50_s) / 1.644854
        network = min(60, self.rng.lognormvariate(math.log(cfg.latency_p50_s), sigma))
        rows = job.symbols * job.bars
        wire = rows * (500 if job.endpoint.startswith("quote") else cfg.wire_row_bytes)
        processing = rows / cfg.parse_rows_s
        disk = 0
        if job.endpoint in ("minute_batch", "minute_single", "daily"):
            write_rows = rows
            if job.client == "marketdata" and job.stream == "ma55":
                # accept() rebuilds the retained raw tail and _write() upserts
                # complete bounded windows, not merely the newly received bar.
                # Native15 uses the same explicit cost assumption for comparison.
                write_rows = job.symbols * cfg.live_rewrite_bars
            elif job.stream in ("COLD", "WARM"):
                period = int(job.period[:-1])
                widths = (5, 15, 30, 60, 120, 240, 480) if period == 5 else (15, 30, 60, 120, 240, 480)
                write_rows = job.symbols * sum(min(144 if width == 5 else 256,
                                                  job.bars * period // width) for width in widths)
            disk = write_rows * cfg.disk_row_bytes
            self.counts["upsert_bar_rows"] += write_rows
            processing += write_rows / cfg.write_rows_s + job.symbols * cfg.transaction_ms / 1000
        if job.stream in ("COLD", "WARM"):
            processing += rows / cfg.history_rows_s
        self.counts["wire_bytes"] += wire
        self.counts["estimated_write_bytes"] += disk
        self.counts["decoded_rows"] += rows
        return network + wire / (cfg.bandwidth_MB_s * 1e6) + processing

    def _release_round(self, schedule, now):
        if now > self.cfg.stop_s:
            return
        name = f"{schedule.stream}:{now:.6f}"
        jobs = []
        target = latest_closed(now - 60)
        bars = sum(p <= target for p in session_points(1))
        for template in schedule.templates:
            job = replace(template, ready_s=now, round_id=name,
                          target_s=target if schedule.stream == "full_1m" else now)
            if job.endpoint in ("minute_batch", "minute_single"):
                job.bars = max(1, bars)
            if job.deadline_s is not None:
                job.deadline_s = now + job.deadline_s
            jobs.append(job)
        self.add(jobs)
        self.rounds[name]["schedule"] = schedule

    def _done(self, job, now, outcome):
        successful = outcome == "ok"
        if successful:
            self.counts["completed" if job.endpoint != "compute" else "computations"] += 1
            if job.deadline_s is not None and now > job.deadline_s:
                self.counts["deadline_missed"] += 1
                self.first_overdue = min(self.first_overdue or now, job.deadline_s)
            if job.stream in ("ma55", "quotes", "focus", "depth"):
                target = job.target_s
                if self.cfg.fault == "late_bar" and job.stream == "ma55" and job.batch == 0:
                    target -= 300
                self.observations[job.stream].append((now, target, job.batch, job.symbols))
            if job.stream == "COLD":
                self.history_ready[job.batch] = now
        elif outcome == "retry":
            delay = 31 if self.counts["last_429"] else 5
            self.retry_delays.append(delay)
            self.cooldown = now + delay
            self.add([replace(job, ready_s=now + delay, retry=job.retry + 1)])
            # add() increments an existing round's left: retry replaces this attempt.
        else:
            self.counts["failed"] += outcome not in ("local_skip", "aborted", "superseded")
            self.counts["failed_" + job.stream] += outcome not in ("aborted", "superseded")
            if job.round_id and outcome != "superseded":
                self.failed_rounds.add(job.round_id)
        if job.stage and outcome != "retry":
            self.stage_remaining[job.stage] -= 1
            if successful and self.stage_remaining[job.stage] == 0:
                self.finished_stages.append(job.stage)
            if not successful and outcome != "aborted":
                self.pipeline.update(status="FAIL", failed_stage=job.stage, finished_s=now)
        if job.round_id:
            state = self.rounds[job.round_id]
            state["left"] -= 1
            if state["left"] == 0:
                schedule = state["schedule"]
                elapsed = now - state["start"]
                self.round_durations[state["stream"]].append(elapsed)
                if job.round_id not in self.failed_rounds:
                    if state["stream"] == "full_1m":
                        self.observations["full_1m"].append((now, job.target_s, -1, self.cfg.universe))
                    if job.round_id == "pipeline":
                        self.pipeline.update(status="PASS_CAPACITY_ONLY", finished_s=now)
                if schedule:
                    if state["stream"] == "full_1m" and elapsed > schedule.interval_s:
                        missed = state["start"] + schedule.interval_s
                        self.first_missed_cadence = min(self.first_missed_cadence or missed, missed)
                    self.missed_slots += (max(0, math.ceil(elapsed / schedule.interval_s) - 1)
                                          if state["stream"] == "full_1m" else 0)
                    next_at = (now + schedule.interval_s if schedule.finish_relative else
                               max(state["start"] + schedule.interval_s, now))
                    next_at = next_session(next_at, quotes=schedule.quotes)
                    if next_at is not None and next_at <= self.cfg.stop_s:
                        self._event(next_at, "round", schedule)

    def _enqueue(self, item, seq, now):
        lane = item.lane or item.client
        queue = self.ready[lane]
        if item.stream == "ma55":
            older = [entry for entry in queue if entry[3].stream == "ma55" and entry[3].batch == item.batch]
            if older:
                # The real worker reselects latest target at batch boundaries.
                # Merge the missing interval into one request, not an old FIFO of
                # scans. In-flight requests finish; they cannot be preempted.
                candidates = older + [(item.priority, item.ready_s, seq, item)]
                newest = max(candidates, key=lambda entry: entry[3].target_s)
                earliest = min(entry[3].target_s for entry in candidates)
                job = newest[3]
                job.bars = max(job.bars, 1 + sum(earliest < t <= job.target_s
                                               for t in session_points(self.cfg.ma_period)))
                queue[:] = [entry for entry in queue if entry not in older]
                heapq.heapify(queue)
                for entry in candidates:
                    if entry is not newest:
                        self.counts["superseded"] += 1
                        self._done(entry[3], now, "superseded")
                heapq.heappush(queue, newest)
                return
        heapq.heappush(queue, (item.priority, item.ready_s, seq, item))

    def run(self):
        now = 0.0
        while now <= self.cfg.stop_s:
            while self.events and self.events[0][0] <= now:
                _, seq, kind, item = heapq.heappop(self.events)
                if kind == "round":
                    self._release_round(item, now)
                elif kind == "release":
                    self._enqueue(item, seq, now)
                else:
                    job, outcome, lane = item
                    self.busy.remove(lane)
                    self._done(job, now, outcome)
            choices, wake = [], []
            for lane, queue in self.ready.items():
                while queue and queue[0][3].round_id in self.failed_rounds:
                    job = heapq.heappop(queue)[3]
                    self.counts["aborted"] += job.endpoint != "compute"
                    self._done(job, now, "aborted")
                if not queue or lane in self.busy:
                    continue
                job = queue[0][3]
                gates = self._gates(job)
                due = max([now, self.cooldown if job.client == "marketdata" else now,
                           *(q.ready(now) for q in gates)])
                if job.nonblocking and due > now:
                    heapq.heappop(queue)
                    self.counts["local_skipped"] += 1
                    self._done(job, now, "local_skip")
                    wake.append(now)
                    continue
                if due <= now:
                    # Aging is a proposed broker policy, current lanes keep their priorities.
                    priority = job.priority
                    if self.cfg.policy == "broker" and priority:
                        priority = max(1, priority - int((now - job.ready_s) / 120))
                    choices.append((priority, job.ready_s, queue[0][2], lane, job, gates))
                else:
                    wake.append(due)
            self.queue_peak = max(self.queue_peak, sum(len(q) for q in self.ready.values()))
            if choices:
                _, _, _, lane, job, gates = min(choices, key=lambda c: c[:3])
                heapq.heappop(self.ready[lane])
                for gate in gates:
                    gate.charge(now)
                outcome, duration = "ok", self._duration(job)
                if job.endpoint != "compute":
                    self.counts["sent"] += 1
                    self.last_http = now
                    refused = False
                    for pool, limit in self._vendor_windows(job):
                        quota = self._window(self.vendor, pool, limit)
                        refused |= quota.ready(now) > now
                        quota.charge(now)
                        self._window(self.attempts, pool, limit).charge(now)
                    injected_429 = (self.cfg.fault == "429" and not self.injected
                                    and now >= at("10:00") and job.client == "marketdata")
                    if refused or injected_429:
                        self.injected |= injected_429
                        self.counts["rejected_429"] += 1
                        self.counts["last_429"] = 1
                        outcome = "retry" if job.client == "marketdata" else "429"
                        duration = 0.05
                    elif self.cfg.fault == "outage" and at("10:00") <= now < at("10:00") + self.cfg.outage_minutes * 60:
                        self.counts["network_timeouts"] += 1
                        self.counts["last_429"] = 0
                        outcome = "retry" if job.client == "marketdata" else "timeout"
                        duration = 30
                    elif self.cfg.fault == "factor_unverified" and job.stage == "factors" and self.stage_remaining["factors"] == 1:
                        outcome = "UNVERIFIED"
                self.busy.add(lane)
                self._event(now + duration, "complete", (job, outcome, lane))
                continue
            if self.events:
                wake.append(self.events[0][0])
            if not wake:
                break
            future = min(wake)
            now = future if future > now else now + 1e-6
        from .metrics import summarize

        self.counts["pending"] = (sum(len(q) for q in self.ready.values()) + len(self.busy)
                                  + sum(e[2] == "release" for e in self.events))
        return summarize(self)

    def parameters(self):
        return asdict(self.cfg)
