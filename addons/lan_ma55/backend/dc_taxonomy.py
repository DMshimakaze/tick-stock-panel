"""EastMoney directory, membership and same-day industry admission."""
from collections import defaultdict
import re

KINDS = ("industry", "concept", "theme")
INDEX_TYPES = {"industry": "行业板块", "concept": "概念板块"}
LEVELS = {"东财一级行业": 1, "东财二级行业": 2, "东财三级行业": 3}
BOARD_CODE = re.compile(r"[A-Z0-9]+\.DC")
DC_KEY = re.compile(r"dc:(?:industry|concept|theme):[A-Z0-9]+\.DC")
SECURITY_CODE = re.compile(r"[A-Z0-9]{1,12}\.[A-Z]{2,6}")
DIRECTORY_FIELDS = "ts_code,trade_date,name,idx_type,level"
HIERARCHY = "DERIVED_FROM_DC_MEMBERSHIP"
MEMBER_FIELDS = {
    "dc_member": "trade_date,ts_code,con_code,name",
    "dc_concept_cons": "trade_date,theme_code,ts_code,name,reason",
}
CAP = {"industry": 5000, "concept": 5000, "theme": 3000}


class DCStructureError(ValueError):
    """Same-day contents do not establish a complete generation."""


def rows(data):
    fields, items = data.get("fields"), data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list) or len(set(fields)) != len(fields):
        raise DCStructureError("DC_INVALID_FIELDS")
    try:
        return [dict(zip(fields, item, strict=True)) for item in items]
    except (TypeError, ValueError):
        raise DCStructureError("DC_INVALID_ROWS") from None


def catalog_request(kind, day):
    if kind == "theme":
        return "dc_concept", {"trade_date": day}, "theme_code,trade_date,name"
    return "dc_index", {"trade_date": day, "idx_type": INDEX_TYPES[kind]}, DIRECTORY_FIELDS


def member_request(kind, keys, day):
    api, code_field = ("dc_concept_cons", "theme_code") if kind == "theme" else ("dc_member", "ts_code")
    params = {"trade_date": day, code_field: ",".join(key.split(":")[-1] for key in keys)}
    if len(keys) > 1:
        params["limit"] = 2500 if kind == "theme" else 4000
    return api, params, MEMBER_FIELDS[api]


def decode_catalog(kind, day, data):
    directory = rows(data)
    if not directory or len(directory) >= 5000:
        raise DCStructureError("DC_EMPTY_OR_CAPPED_DIRECTORY")
    code_field = "theme_code" if kind == "theme" else "ts_code"
    nodes = {}
    for row in directory:
        code, name = row.get(code_field), row.get("name")
        if (not isinstance(code, str) or not BOARD_CODE.fullmatch(code)
                or not isinstance(name, str) or not name.strip() or row.get("trade_date") != day
                or (kind in INDEX_TYPES and row.get("idx_type") != INDEX_TYPES[kind])):
            raise DCStructureError("DC_INVALID_DIRECTORY_IDENTITY")
        key = f"dc:{kind}:{code}"
        if key in nodes:
            raise DCStructureError("DC_DUPLICATE_DIRECTORY_CODE")
        source_level = row.get("level")
        level = LEVELS.get(source_level) if kind == "industry" else None
        if kind == "industry" and level is None:
            raise DCStructureError("DC_UNKNOWN_INDUSTRY_LEVEL")
        nodes[key] = dict(key=key, code=code, kind=kind, name=name.strip(),
                          level=level, source_level=source_level)
    return nodes


def decode_members(kind, keys, day, data, *, limit=None):
    values = rows(data)
    if len(values) >= min(CAP[kind], limit or CAP[kind]):
        raise DCStructureError("DC_MEMBER_PAGE_LIMIT")
    code_field, symbol_field = ("theme_code", "ts_code") if kind == "theme" else ("ts_code", "con_code")
    members = {key: {} for key in keys}
    for row in values:
        key = f"dc:{kind}:{row.get(code_field)}"
        symbol, name, reason = row.get(symbol_field), row.get("name"), row.get("reason")
        if (key not in members or row.get("trade_date") != day or not isinstance(symbol, str)
                or not SECURITY_CODE.fullmatch(symbol) or not isinstance(name, str) or not name.strip()
                or (reason is not None and not isinstance(reason, str))):
            raise DCStructureError("DC_UNEXPECTED_MEMBER")
        if symbol in members[key]:
            raise DCStructureError("DC_DUPLICATE_MEMBER")
        members[key][symbol] = (name.strip(), reason)
    if len(keys) > 1 and any(not group for group in members.values()):
        raise DCStructureError("DC_MISSING_BATCH_GROUP")
    return members


def derive_industry(nodes, memberships, scope):
    """Resolve parent edges with raw members before comparing the approved A-share scope."""
    by_level = {n: {} for n in (1, 2, 3)}
    industry = {key: node for key, node in nodes.items() if node["kind"] == "industry"}
    for key, node in industry.items():
        members = memberships.get(key)
        if not members:
            raise DCStructureError(f"DC_EMPTY_OR_PENDING_INDUSTRY {key}")
        level = by_level[node["level"]]
        for symbol in members:
            if symbol in level:
                raise DCStructureError(f"DC_OVERLAPPING_INDUSTRY {node['level']} {level[symbol]} {key}")
            level[symbol] = key
    level_sets = [set(members) for members in by_level.values()]
    if not level_sets[0] or len({frozenset(members) for members in level_sets}) != 1:
        raise DCStructureError("DC_MISSING_INDUSTRY_LEVEL_MEMBERSHIP")
    parents = {}
    child_union = defaultdict(set)
    for key, node in industry.items():
        level = node["level"]
        if level == 1:
            continue
        members = memberships[key]
        direct = {by_level[level - 1][symbol] for symbol in members}
        if len(direct) != 1:
            raise DCStructureError(f"DC_INDUSTRY_PARENT_UNRESOLVED {key}")
        parent, = direct
        parents[key] = parent
        child_union[parent].update(members)
    for key, node in industry.items():
        if node["level"] < 3 and set(memberships[key]) != child_union[key]:
            raise DCStructureError(f"DC_INCOMPLETE_CHILD_UNION {key}")
    return parents, sorted(set(scope) - level_sets[0])
