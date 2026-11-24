"""Use the configured HTTPS relay for EastMoney classification only."""
from time import time
from urllib.parse import urlsplit

import httpx

from .dc_taxonomy import DCStructureError
from .market_grid import as_ms, now_cn
from .request_budget import RollingBudget, retry_seconds


class DCUnavailable(RuntimeError):
    def __init__(self, reason, retry_at=0):
        super().__init__(reason)
        self.retry_at = retry_at


class DCTransport:
    def __init__(self, token, endpoint, stop, limit=18, *, transport=None):
        self.token = token
        self.endpoint = endpoint
        self.stop = stop
        self.budget = RollingBudget(limit)
        self.client = httpx.Client(timeout=20, follow_redirects=False, transport=transport)
        self.auth_error = None
        self.last_receipt = None
        url = urlsplit(endpoint or "")
        if (not token or url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment):
            self.auth_error = "DC_GATEWAY_REQUIRED"

    def request(self, api, params, fields):
        self.last_receipt = None
        if self.auth_error:
            raise DCUnavailable(self.auth_error)
        if self.budget.delay() > 0:
            raise DCUnavailable("DC_LOCAL_BUDGET", time() + self.budget.delay())
        self.budget.acquire(self.stop)
        receipt = {"api": api, "params": params, "http": None, "business_code": None, "rows": None,
                   "observed_ms": None, "source_ms": None, "error": None}
        try:
            response = self.client.post(self.endpoint, json={
                "api_name": api, "token": self.token, "params": params, "fields": fields,
            })
            status = receipt["http"] = response.status_code
            if status in (401, 403):
                self.auth_error = "DC_AUTHORIZATION"
                raise DCUnavailable(self.auth_error)
            if status == 429:
                raise DCUnavailable("DC_RATE_LIMIT", time() + max(60, retry_seconds(header=response.headers.get("Retry-After"))))
            if status != 200:
                raise DCUnavailable(f"DC_HTTP_{status}", time() + 60)
            try:
                payload = response.json()
            except ValueError:
                raise DCStructureError("DC_INVALID_JSON") from None
            if not isinstance(payload, dict) or type(payload.get("code")) is not int:
                raise DCStructureError("DC_INVALID_ENVELOPE")
            code = receipt["business_code"] = payload["code"]
            if code != 0:
                message = str(payload.get("msg", "")).casefold()
                if any(word in message for word in ("token", "权限", "积分", "auth")):
                    self.auth_error = "DC_AUTHORIZATION"
                    raise DCUnavailable(self.auth_error)
                if any(word in message for word in ("频次", "频率", "限流", "rate limit")):
                    raise DCUnavailable("DC_RATE_LIMIT", time() + 60)
                raise DCStructureError(f"DC_BUSINESS_{code}")
            data = payload.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise DCStructureError("DC_INVALID_DATA")
            receipt["rows"] = len(data["items"])
            return data
        except httpx.TimeoutException:
            receipt["error"] = "DC_TIMEOUT"
            raise DCUnavailable("DC_TIMEOUT", time() + 60) from None
        except httpx.HTTPError:
            receipt["error"] = "DC_NETWORK"
            raise DCUnavailable("DC_NETWORK", time() + 60) from None
        except (DCUnavailable, DCStructureError) as exc:
            receipt["error"] = str(exc)
            raise
        finally:
            receipt["observed_ms"] = as_ms(now_cn())
            self.last_receipt = receipt

    def close(self):
        self.client.close()
