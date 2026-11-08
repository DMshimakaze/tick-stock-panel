"""Own relation normalization based on documented KPL field shapes."""

from .schema import (
    InvalidSnapshot,
    Symbols,
    array,
    encoded,
    identity,
    object_value,
    stock,
    text,
    timestamp,
)


def normalize_info(raw, *, asof, source_kind="KPL_INFOGET", known_symbols=()):
    raw = object_value(raw, "InfoGet")
    if str(raw.get("errcode")) != "0":
        raise InvalidSnapshot("SOURCE_ERROR: errcode must be explicit zero")
    theme_id = identity(raw.get("ID"), "ID")
    theme = dict(
        provider="KPL",
        theme_id=theme_id,
        theme_name=text(raw.get("Name"), "Name", required=True),
        brief=text(raw.get("BriefIntro"), "BriefIntro"),
        source_created_at=timestamp(raw.get("CreateTime"), "CreateTime", optional=True),
        source_updated_at=timestamp(raw.get("UpdateTime"), "UpdateTime", optional=True),
        snapshot_asof=asof,
        source_kind=source_kind,
        class_layer=identity(raw["ClassLayer"], "ClassLayer")
        if "ClassLayer" in raw
        else None,
    )
    nodes, members, node_ids = {}, {}, {}
    symbols = Symbols(known_symbols)

    def node(value, parent, field):
        value = object_value(value, field)
        name = text(value.get("Name"), field + ".Name", required=True)
        source_id = (
            identity(value["ID"], field + ".ID")
            if value.get("ID") not in (None, "")
            else None
        )
        path = (nodes[parent]["identity_path"] if parent else []) + [
            field + ":" + ("id=" + source_id if source_id else "name=" + name)
        ]
        key = encoded(path)
        entry = dict(
            key=key,
            node_id=source_id,
            parent_id=parent,
            name=name,
            depth=2 if parent else 1,
            source_field=field,
            identity_path=path,
            path=(nodes[parent]["path"] if parent else []) + [name],
        )
        if key in nodes and nodes[key] != entry:
            raise InvalidSnapshot("CONFLICTING_NODE_ID")
        nodes[key] = entry
        if source_id:
            node_ids.setdefault(source_id, set()).add(key)
        return key

    def member(value, branch, source_field):
        value = stock(value)
        symbol = symbols.resolve(value["StockID"])
        stock_key = symbol or value["StockID"]
        key = (branch, stock_key)
        record = dict(
            provider="KPL",
            theme_id=theme_id,
            branch_path_key=branch,
            branch_path=nodes[branch]["path"] if branch else [],
            stock_identity=stock_key,
            symbol=symbol,
            symbol_status="RESOLVED" if symbol else "UNRESOLVED_SYMBOL",
            **value,
            source_fields=[source_field],
        )
        if key in members:
            previous = members[key]
            previous["source_fields"] = sorted(
                set(previous["source_fields"] + [source_field])
            )
            previous["Tag"] = list(
                {encoded(t): t for t in previous["Tag"] + record["Tag"]}.values()
            )
            for field in ("prod_name", "Reason", "IsZz", "IsHot"):
                incoming, old = record.get(field), previous.get(field)
                if old in (None, ""):
                    previous[field] = incoming
                elif incoming not in (None, "") and old != incoming:
                    previous.setdefault("attribute_variants", {}).setdefault(
                        field, [old]
                    )
                    if incoming not in previous["attribute_variants"][field]:
                        previous["attribute_variants"][field].append(incoming)
        else:
            members[key] = record

    # ClassLayer is retained, never used to discard the actual Table hierarchy.
    for row in array(raw.get("Table", []), "Table"):
        row = object_value(row, "Table item")
        first = object_value(row.get("Level1"), "Level1")
        parent = node(first, None, "Level1")
        for value in array(first.get("Stocks", []), "Level1.Stocks"):
            member(value, parent, "Level1.Stocks")
        for second in array(row.get("Level2", []), "Level2"):
            child = node(second, parent, "Level2")
            for value in array(second.get("Stocks", []), "Level2.Stocks"):
                member(value, child, "Level2.Stocks")
    for field in ("StockList", "Stocks"):
        for value in array(raw.get(field, []), field):
            checked = stock(value)
            matches = {
                next(iter(node_ids[t["ID"]]))
                for t in checked["Tag"]
                if t["ID"] in node_ids and len(node_ids[t["ID"]]) == 1
            }
            if matches:
                for branch in sorted(matches):
                    member(checked, branch, field + ".Tag")
            else:
                member(checked, None, field)
    if not members:
        raise InvalidSnapshot("EMPTY_THEME_MEMBERSHIP")
    return dict(**theme, nodes=list(nodes.values()), memberships=list(members.values()))


def normalize_export(rows, *, asof, known_symbols=()):
    """Version-1 export rows carry paths explicitly; they are not InfoGet responses."""
    grouped = {}
    for row in rows:
        row = object_value(row, "export row")
        tid = identity(row.get("theme_id"), "theme_id")
        name = text(row.get("theme_name"), "theme_name", required=True)
        path = row.get("branch_path")
        if isinstance(path, str):
            if path.startswith("["):
                import json

                try:
                    path = json.loads(path)
                except ValueError:
                    raise InvalidSnapshot("INVALID_BRANCH_PATH") from None
            else:
                path = path.split(" / ") if path else []
        path = [
            text(p, "branch_path", required=True) for p in array(path, "branch_path")
        ]
        if len(path) > 2:
            raise InvalidSnapshot("EXPORT_V1_SUPPORTS_TWO_BRANCH_LEVELS")
        ids = row.get("branch_ids")
        if isinstance(ids, str) and ids:
            import json

            try:
                ids = json.loads(ids)
            except ValueError:
                raise InvalidSnapshot("INVALID_BRANCH_IDS") from None
        ids = ids or [None] * len(path)
        if not isinstance(ids, list) or len(ids) != len(path):
            raise InvalidSnapshot("INVALID_BRANCH_IDS")
        item = grouped.setdefault(
            tid, dict(ID=tid, Name=name, errcode="0", Table=[], Stocks=[])
        )
        if item["Name"] != name:
            raise InvalidSnapshot("CONFLICTING_THEME_NAME")
        value = dict(
            StockID=row.get("stock_code"),
            prod_name=row.get("prod_name", ""),
            Reason=row.get("Reason", ""),
        )
        for flag in ("IsZz", "IsHot"):
            if flag in row:
                value[flag] = row[flag]
        if not path:
            item["Stocks"].append(value)
            continue
        first = dict(Name=path[0], Stocks=[])
        if ids[0] is not None:
            first["ID"] = ids[0]
        table = dict(Level1=first, Level2=[])
        if len(path) == 1:
            first["Stocks"].append(value)
        else:
            second = dict(Name=path[1], Stocks=[value])
            if ids[1] is not None:
                second["ID"] = ids[1]
            table["Level2"].append(second)
        item["Table"].append(table)
    return [
        normalize_info(
            raw, asof=asof, source_kind="AUTHORIZED_EXPORT", known_symbols=known_symbols
        )
        for raw in grouped.values()
    ]
