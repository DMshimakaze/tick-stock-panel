"""Authorized local inputs only; SourceAdapter is the input boundary, not a KPL client."""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Protocol

from .normalize import normalize_info, normalize_export
from .schema import (
    InvalidSnapshot,
    MAX_INPUT_BYTES,
    MAX_THEMES,
    VERSION,
    encoded,
    identity,
    now_cn,
    object_value,
    timestamp,
)


class SourceAdapter(Protocol):
    def documents(self) -> list[tuple[str, bytes]]: ...


class FileSource:
    def __init__(self, path, manifest=None):
        self.path = Path(path)
        try:
            self.manifest = (
                object_value(
                    json.loads(Path(manifest).read_text(encoding="utf-8-sig")),
                    "manifest",
                )
                if manifest
                else None
            )
        except (ValueError, OSError):
            raise InvalidSnapshot("INVALID_OR_UNAVAILABLE_MANIFEST") from None
        if self.manifest is not None:
            if (
                self.manifest.get("format") != "lan-theme-manifest"
                or self.manifest.get("format_version") != VERSION
            ):
                raise InvalidSnapshot("MANIFEST_VERSION_UNSUPPORTED")
            self.base = Path(manifest).resolve().parent
        else:
            self.base = (
                self.path.resolve()
                if self.path.is_dir()
                else self.path.resolve().parent
            )

    def documents(self):
        if self.manifest is not None:
            paths = []
            names = self.manifest.get("files")
            if not isinstance(names, list) or any(
                not isinstance(n, str) for n in names
            ):
                raise InvalidSnapshot("INVALID_MANIFEST_FILES")
            for name in names:
                path = (self.base / name).resolve()
                if not path.is_relative_to(self.base):
                    raise InvalidSnapshot("MANIFEST_PATH_OUTSIDE_INPUT")
                paths.append(path)
        elif self.path.is_dir():
            paths = sorted(
                p
                for p in self.path.iterdir()
                if p.suffix.lower() in (".json", ".jsonl", ".csv")
            )
        else:
            paths = [self.path]
        if not paths or len(paths) > MAX_THEMES:
            raise InvalidSnapshot("EMPTY_OR_OVERSIZED_INPUT")
        size, result = 0, []
        for path in paths:
            if path.suffix.lower() not in (".json", ".jsonl", ".csv"):
                raise InvalidSnapshot("UNSUPPORTED_INPUT_FORMAT")
            try:
                if size + path.stat().st_size > MAX_INPUT_BYTES:
                    raise InvalidSnapshot("INPUT_EXCEEDS_32_MIB")
                body = path.read_bytes()
            except OSError:
                raise InvalidSnapshot("INPUT_FILE_UNAVAILABLE: " + path.name) from None
            size += len(body)
            if size > MAX_INPUT_BYTES:
                raise InvalidSnapshot("INPUT_EXCEEDS_32_MIB")
            result.append((path.name, body))
        return result


def decode_document(name, body, *, asof, known_symbols, export_version):
    try:
        value = body.decode("utf-8-sig")
        if name.lower().endswith(".csv"):
            if export_version != VERSION:
                raise InvalidSnapshot("CSV_REQUIRES_EXPLICIT_EXPORT_VERSION_1")
            reader = csv.DictReader(io.StringIO(value), strict=True)
            if not {"theme_id", "theme_name", "branch_path", "stock_code"} <= set(
                reader.fieldnames or []
            ):
                raise InvalidSnapshot("CSV_REQUIRED_COLUMNS_MISSING")
            return normalize_export(
                list(reader), asof=asof, known_symbols=known_symbols
            )
        if name.lower().endswith(".jsonl"):
            lines = [json.loads(line) for line in value.splitlines() if line.strip()]
            header = lines[0] if lines else {}
            rows = lines[1:]
        else:
            header = json.loads(value)
            if isinstance(header, dict) and header.get("format") == "lan-theme-export":
                rows = header.get("rows")
            else:
                return [normalize_info(header, asof=asof, known_symbols=known_symbols)]
        header = object_value(header, "export header")
        if header.get("asof_cn") and timestamp(header["asof_cn"]) != asof:
            raise InvalidSnapshot("EXPORT_ASOF_CONFLICT")
        if (
            header.get("format") != "lan-theme-export"
            or header.get("format_version") != VERSION
        ):
            raise InvalidSnapshot("EXPORT_VERSION_UNSUPPORTED")
        if not isinstance(rows, list) or not rows:
            raise InvalidSnapshot("EMPTY_EXPORT")
        return normalize_export(rows, asof=asof, known_symbols=known_symbols)
    except (UnicodeError, json.JSONDecodeError, csv.Error):
        raise InvalidSnapshot("INVALID_JSON_OR_UTF8") from None


def import_source(
    store,
    adapter: SourceAdapter,
    *,
    asof,
    source,
    known_symbols=(),
    export_version=None,
    demo=False,
    demo_allowed=False,
):
    report = dict(
        expected=None,
        fetched=0,
        valid=0,
        missing=0,
        missing_ids=[],
        rejected=0,
        errors={},
    )
    try:
        asof = timestamp(asof)
        if not isinstance(source, str) or not source.strip():
            raise InvalidSnapshot("SOURCE_PROVENANCE_REQUIRED")
        if demo and not demo_allowed:
            raise InvalidSnapshot("DEMO_NOT_ALLOWED: use an isolated data directory")
        documents = adapter.documents()
        report["fetched"] = len(documents)
        manifest = getattr(adapter, "manifest", None) or {}
        expected = manifest.get("expected_theme_ids")
        if expected is not None:
            if not isinstance(expected, list) or not expected:
                raise InvalidSnapshot("INVALID_EXPECTED_THEME_IDS")
            expected = [identity(t, "expected_theme_id") for t in expected]
            if len(expected) != len(set(expected)):
                raise InvalidSnapshot("DUPLICATE_EXPECTED_THEME_ID")
            report["expected"] = len(expected)
        # This source hash skips repeat normalization/publication of an identical
        # import and is retained as the requested provenance receipt.
        digest = hashlib.sha256(
            encoded(
                dict(
                    asof=asof,
                    source=source,
                    manifest=manifest,
                    demo=demo,
                    export_version=export_version,
                    known_symbols=sorted(set(known_symbols)),
                )
            ).encode()
        )
        for name, body in documents:
            digest.update(encoded([name, len(body)]).encode())
            digest.update(body)
        source_hash = digest.hexdigest()
        existing = store.existing(source_hash)
        if existing:
            return store.save(existing)
        themes = []
        for name, body in documents:
            try:
                parsed = decode_document(
                    name,
                    body,
                    asof=asof,
                    known_symbols=known_symbols,
                    export_version=export_version,
                )
                themes.extend(parsed)
            except InvalidSnapshot as exc:
                report["rejected"] += 1
                report["errors"][name] = str(exc)
        ids = [t["theme_id"] for t in themes]
        report["valid"] = len(set(ids))
        if len(ids) != len(set(ids)):
            report["rejected"] += 1
            report["errors"]["theme_id"] = "DUPLICATE_THEME_ID"
        if expected is not None:
            report["missing_ids"] = sorted(set(expected) - set(ids))
            report["missing"] = len(report["missing_ids"])
            if set(ids) - set(expected):
                report["rejected"] += 1
                report["errors"]["manifest"] = "UNEXPECTED_THEME_ID"
        if (
            report["missing"]
            or report["rejected"]
            or not themes
            or len(themes) > MAX_THEMES
        ):
            raise InvalidSnapshot("IMPORT_REJECTED: incomplete or invalid detail set")
        coverage = "MANIFEST_COMPLETE" if expected is not None else "UNKNOWN"
        reason = (
            "All explicitly declared IDs validated; KPL global catalog completeness is not independently established"
            if expected is not None
            else "No authoritative catalog manifest; partial/unknown coverage"
        )
        for theme in themes:
            theme["catalog_coverage"] = coverage
        kinds = sorted({t["source_kind"] for t in themes})
        counts = dict(
            themes=len(themes),
            nodes=sum(len(t["nodes"]) for t in themes),
            memberships=sum(len(t["memberships"]) for t in themes),
            source_stock_identities=len(
                {m["stock_identity"] for t in themes for m in t["memberships"]}
            ),
            unresolved_memberships=sum(
                m["symbol"] is None for t in themes for m in t["memberships"]
            ),
        )
        snapshot = dict(
            format="lan-theme-snapshot",
            format_version=VERSION,
            snapshot_id="snapshot-" + source_hash,
            source_hash=source_hash,
            asof_cn=asof,
            imported_at=now_cn(),
            source=source.strip(),
            source_kind=kinds[0] if len(kinds) == 1 else "MIXED_LOCAL_SNAPSHOT",
            scope=manifest.get("scope", "PARTIAL_UNKNOWN"),
            catalog_coverage=coverage,
            coverage_reason=reason,
            coverage=report,
            counts=counts,
            demo=demo,
            themes=themes,
        )
        snapshot.update(
            source_count=report["fetched"],
            valid_count=report["valid"],
            rejected_count=report["rejected"],
        )
        if len(encoded(snapshot).encode()) > MAX_INPUT_BYTES:
            raise InvalidSnapshot("NORMALIZED_SNAPSHOT_EXCEEDS_32_MIB")
        return store.save(snapshot)
    except (InvalidSnapshot, OSError) as exc:
        report["result"] = "REJECTED"
        report["error"] = (
            str(exc) if isinstance(exc, InvalidSnapshot) else "LOCAL_SOURCE_UNAVAILABLE"
        )
        store.reject(report)
        raise InvalidSnapshot(encoded(report)) from None
