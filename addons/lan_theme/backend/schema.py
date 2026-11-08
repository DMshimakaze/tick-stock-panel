"""Versioned input boundaries shared by file import and normalization."""

from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo

VERSION = 1
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_THEMES = 2000
BEIJING = ZoneInfo("Asia/Shanghai")
SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")


class InvalidSnapshot(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def now_cn():
    return datetime.now(BEIJING).replace(tzinfo=None, microsecond=0).isoformat(" ")


def timestamp(value, field="asof_cn", *, optional=False):
    if optional and value in (None, ""):
        return None
    try:
        if isinstance(value, str) and re.fullmatch(r"\d{10}|\d{13}", value):
            value = int(value)
        if type(value) in (int, float):
            if value < 0:
                raise ValueError()
            date = datetime.fromtimestamp(
                value / (1000 if value >= 10**12 else 1), BEIJING
            )
        elif isinstance(value, str):
            date = datetime.fromisoformat(value)
        else:
            raise ValueError()
        if date.tzinfo:
            date = date.astimezone(BEIJING)
        return date.replace(tzinfo=None, microsecond=0).isoformat(" ")
    except (ValueError, OverflowError, OSError):
        raise InvalidSnapshot(f"INVALID_TIME: {field}") from None


def text(value, field, *, required=False):
    if value is None and not required:
        return ""
    if (
        not isinstance(value, str)
        or (required and not value.strip())
        or len(value) > 32768
    ):
        raise InvalidSnapshot(f"INVALID_TEXT: {field}")
    return value.strip()


def identity(value, field):
    if type(value) not in (str, int) or not str(value).strip():
        raise InvalidSnapshot(f"INVALID_ID: {field}")
    return str(value).strip()


def array(value, field):
    if not isinstance(value, list):
        raise InvalidSnapshot(f"INVALID_ARRAY: {field}")
    return value


def object_value(value, field):
    if not isinstance(value, dict):
        raise InvalidSnapshot(f"INVALID_OBJECT: {field}")
    return value


def stock(value):
    value = object_value(value, "stock")
    code = text(value.get("StockID"), "StockID", required=True).upper()
    if not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?|(?:SH|SZ|BJ)\d{6}", code):
        raise InvalidSnapshot("INVALID_STOCK_ID")
    result = dict(StockID=code, prod_name=text(value.get("prod_name"), "prod_name"))
    for field in ("IsZz", "IsHot"):
        if field in value:
            flag = value[field]
            if flag is not None and type(flag) not in (str, int, bool):
                raise InvalidSnapshot(f"INVALID_FLAG: {field}")
            result[field] = flag
    result["Reason"] = text(value.get("Reason"), "Reason")
    tags = []
    for tag in array(value.get("Tag", []), "Tag"):
        tag = object_value(tag, "Tag item")
        tags.append(
            dict(
                ID=identity(tag["ID"], "Tag.ID")
                if tag.get("ID") not in (None, "")
                else None,
                Name=text(tag.get("Name"), "Tag.Name"),
                Reason=text(tag.get("Reason"), "Tag.Reason"),
            )
        )
    result["Tag"] = tags
    return result


class Symbols:
    def __init__(self, known=()):
        self.by_code = {}
        for symbol in known:
            if SYMBOL.fullmatch(symbol):
                self.by_code.setdefault(symbol[:6], set()).add(symbol)

    def resolve(self, code):
        if SYMBOL.fullmatch(code):
            return code
        if re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", code):
            return code[2:] + "." + code[:2]
        candidates = self.by_code.get(code, set())
        return next(iter(candidates)) if len(candidates) == 1 else None
