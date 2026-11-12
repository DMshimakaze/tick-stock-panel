"""Public lan.ma55 HTTP v1 client, independent of its implementation."""

import httpx

PREFIX = "/api/extensions/lan.ma55/v1"


class Client:
    def __init__(self, base, *, session=None, token=None, transport=None):
        self.requests = 0
        self.http = httpx.Client(
            base_url=base,
            timeout=30,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + token} if token else {},
            cookies={"tf_session": session} if session else {},
        )

    def get(self, path, **params):
        self.requests += 1
        response = self.http.get(PREFIX + path, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("api_version") != 1:
            raise ValueError("lan.ma55 API version mismatch; v1 required")
        return data

    def status(self):
        return self.get("/status")

    def universe(self, offset=0):
        return self.get("/universe", offset=offset, limit=100)

    def bars(self, symbols, *, timeframes="15m", asof=None):
        return self.get(
            "/bars",
            symbols=",".join(symbols),
            timeframes=timeframes,
            **({"asof": asof} if asof else {}),
        )

    def events(self, symbol, start, end):
        rows, total, offset, symbol_total = [], 0, 0, 0
        # A visible 250-bar window has a bounded paginated event response.
        for _ in range(16):
            page = self.get("/events",symbol=symbol,start=start,end=end,offset=offset,limit=200)
            rows.extend(page["rows"])
            total = page["total"]
            symbol_total = page.get("symbol_total", total)
            offset += len(page["rows"])
            if offset >= total or not page["rows"]:
                break
        return dict(rows=rows,total=total,truncated=offset<total,outside_window=max(0,symbol_total-total))

    def close(self):
        self.http.close()
