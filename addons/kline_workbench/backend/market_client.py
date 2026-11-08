"""Public lan.marketdata HTTP v1 client, independent of its implementation."""

import httpx

PREFIX = "/api/extensions/lan.marketdata/v1"


class Client:
    def __init__(self, base, *, session=None, token=None, transport=None):
        self.http = httpx.Client(
            base_url=base,
            timeout=30,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + token} if token else {},
            cookies={"tf_session": session} if session else {},
        )

    def get(self, path, **params):
        response = self.http.get(PREFIX + path, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("api_version") != 1:
            raise ValueError("lan.marketdata API version mismatch; v1 required")
        return data

    def universe(self, offset=0):
        return self.get("/universe", offset=offset, limit=100)

    def bars(self, symbols, *, timeframes="15m", asof=None):
        return self.get(
            "/bars",
            symbols=",".join(symbols),
            timeframes=timeframes,
            **({"asof": asof} if asof else {}),
        )

    def close(self):
        self.http.close()
