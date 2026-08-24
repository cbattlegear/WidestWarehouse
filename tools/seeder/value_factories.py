from __future__ import annotations

import datetime as dt
import random
import re
import uuid
from functools import lru_cache
from decimal import Decimal, ROUND_DOWN
from typing import Any

from tools.generator.model_loader import Column

_TYPE_RE = re.compile(r"^(?P<base>[a-z0-9]+)(?:\((?P<a>max|\d+)(?:,(?P<b>\d+))?\))?$", re.I)

WORDS = [
    "Atlas", "Beacon", "Cedar", "Delta", "Evergreen", "Forge", "Granite", "Harbor",
    "Ion", "Junction", "Keystone", "Liberty", "Meridian", "Northstar", "Orion", "Pioneer",
    "Quartz", "Riverton", "Summit", "Titan", "Unity", "Vertex", "Willow", "Zenith",
]
FIRST_NAMES = ["Alex", "Blair", "Casey", "Devon", "Emerson", "Finley", "Harper", "Jordan", "Morgan", "Quinn", "Riley", "Taylor"]
LAST_NAMES = ["Adams", "Bennett", "Chen", "Diaz", "Evans", "Garcia", "Hughes", "Khan", "Martin", "Patel", "Reed", "Singh"]
CURRENCY = ["USD", "EUR", "GBP", "CAD", "MXN", "JPY"]
COUNTRY = ["US", "CA", "MX", "DE", "GB", "JP", "CN", "IN"]


@lru_cache(maxsize=None)
def type_info(type_name: str) -> tuple[str, int | None, int | None]:
    m = _TYPE_RE.match(type_name.strip())
    if not m:
        return type_name.lower(), None, None
    a = None if m.group("a") in (None, "max") else int(m.group("a"))
    b = None if m.group("b") is None else int(m.group("b"))
    return m.group("base").lower(), a, b


def max_length(type_name: str) -> int | None:
    base, a, _ = type_info(type_name)
    if base in {"nvarchar", "char"}:
        return a
    return None


def clamp_text(value: str, col: Column) -> str:
    limit = max_length(col.type)
    if limit is not None and len(value) > limit:
        return value[:limit]
    return value


def decimal_text(value: Decimal | float | int, precision: int | None, scale: int | None) -> str:
    scale = 4 if scale is None else scale
    precision = 18 if precision is None else precision
    max_int_digits = max(1, precision - scale)
    max_abs = Decimal(10) ** max_int_digits - (Decimal(1) / (Decimal(10) ** scale))
    dec = Decimal(str(value))
    if dec > max_abs:
        dec = max_abs
    if dec < -max_abs:
        dec = -max_abs
    quant = Decimal(1) / (Decimal(10) ** scale)
    # format(..., "f") avoids scientific notation (e.g. "0E-8"), which BULK INSERT rejects.
    return format(dec.quantize(quant, rounding=ROUND_DOWN), "f")


def unknown_value(col: Column) -> Any:
    base, a, b = type_info(col.type)
    name = col.name
    if name.endswith("Key"):
        return -1
    if base in {"int", "bigint", "smallint", "tinyint"}:
        return 0
    if base in {"decimal", "money"}:
        return decimal_text(0, a, b if base == "decimal" else 4)
    if base == "float":
        return "0"
    if base == "bit":
        return 0
    if base == "date":
        return "1900-01-01"
    if base == "datetime2":
        return "1900-01-01T00:00:00.000"
    if base == "time":
        return "00:00:00"
    if base in {"binary", "varbinary"}:
        n = a or 32
        return "00" * n
    if base == "uniqueidentifier":
        return "00000000-0000-0000-0000-000000000000"
    if name.endswith("Name"):
        return clamp_text("Unknown", col)
    if name.endswith("Code") or name.endswith("Number"):
        return clamp_text("UNKNOWN", col)
    if "Description" in name or "Message" in name or "Text" in name:
        return clamp_text("N/A", col)
    return clamp_text("N/A", col)


def code_for(table: str, col: Column, seq: int) -> str:
    stem = re.sub(r"[^A-Z0-9]", "", "".join(ch for ch in table if ch.isupper()) or table.upper())[:8]
    if col.name.endswith("CurrencyCode") or col.name == "DefaultCurrencyCode":
        return clamp_text(CURRENCY[seq % len(CURRENCY)], col)
    if col.name.endswith("CountryCode"):
        return clamp_text(COUNTRY[seq % len(COUNTRY)], col)
    width = 6
    return clamp_text(f"{stem}{seq:0{width}d}", col)


def value_for(col: Column, table: str, seq: int, rng: random.Random) -> Any:
    base, a, b = type_info(col.type)
    name = col.name
    lname = name.lower()
    if col.default is not None and name in {"SourceSystemKey", "BatchId"}:
        return -1 if name == "SourceSystemKey" else 0
    if base in {"nvarchar", "char"}:
        if name.endswith("Code") or name.endswith("Number") or "Code" in name or "Number" in name:
            return code_for(table, col, seq)
        if name.endswith("Name") or "Owner" in name or "President" in name or "RequestedBy" in name:
            if "employee" in lname or "owner" in lname or "president" in lname or "byname" in lname:
                text = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            else:
                text = f"{rng.choice(WORDS)} {table} {seq}"
            return clamp_text(text, col)
        if "description" in lname:
            return clamp_text(f"{table} starter record {seq}", col)
        if "email" in lname:
            return clamp_text(f"user{seq}@example.com", col)
        if "phone" in lname:
            return clamp_text(f"+1-555-{seq % 10000:04d}", col)
        if "timezone" in lname:
            return clamp_text("UTC", col)
        if "currency" in lname and (a == 3 or name.endswith("Code")):
            return clamp_text(CURRENCY[seq % len(CURRENCY)], col)
        return clamp_text(f"{table}-{name}-{seq}", col)
    if base in {"int", "bigint", "smallint", "tinyint"}:
        if "sort" in lname or "sequence" in lname or "ordinal" in lname:
            return seq
        if "year" in lname:
            return 2020 + (seq % 10)
        if "month" in lname or "period" in lname:
            return 1 + (seq % 12)
        if "day" in lname:
            return 1 + (seq % 28)
        if "count" in lname:
            return 1 + (seq % 1000)
        if "minutes" in lname:
            return 5 + (seq % 480)
        if "hours" in lname:
            return 1 + (seq % 24)
        return seq
    if base == "bit":
        if name.startswith("Is") or name.endswith("Flag"):
            return 1 if seq % 5 else 0
        return seq % 2
    if base in {"decimal", "money"}:
        scale = b if base == "decimal" else 4
        if any(token in lname for token in ("percent", "factor", "rate")):
            val = Decimal(seq % 1000) / Decimal(10000)
        elif any(token in lname for token in ("amount", "cost", "price", "value")):
            val = Decimal(1000 + (seq * 137) % 900000) / Decimal(100)
        elif "quantity" in lname or "qty" in lname:
            val = Decimal(1 + (seq * 7) % 10000) / Decimal(10)
        elif "minutes" in lname:
            val = Decimal(5 + (seq % 600))
        elif "hours" in lname:
            val = Decimal(1 + (seq % 24))
        else:
            val = Decimal(1 + (seq % 1000))
        return decimal_text(val, a if base == "decimal" else 19, scale)
    if base == "float":
        return f"{(seq % 1000) / 10:.3f}"
    if base == "date":
        d = dt.date(2020, 1, 1) + dt.timedelta(days=seq % 1460)
        if "end" in lname or "to" in lname or "retirement" in lname:
            d += dt.timedelta(days=365)
        return d.isoformat()
    if base == "datetime2":
        d = dt.datetime(2024, 1, 1, 8, 0, 0) + dt.timedelta(minutes=seq)
        return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]
    if base == "time":
        seconds = (seq * 60) % 86400
        return str(dt.time(seconds // 3600, (seconds // 60) % 60, seconds % 60))
    if base in {"binary", "varbinary"}:
        n = a or 32
        raw = uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{name}:{seq}").bytes
        return (raw * ((n // len(raw)) + 1))[:n].hex()
    if base == "uniqueidentifier":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{name}:{seq}"))
    return str(seq)
