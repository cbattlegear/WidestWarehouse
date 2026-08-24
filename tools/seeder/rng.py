from __future__ import annotations

import hashlib
import random

BASE_SEED = 42


def table_seed(table_name: str, base_seed: int = BASE_SEED) -> int:
    digest = hashlib.sha256(f"{base_seed}:{table_name}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def table_rng(table_name: str, base_seed: int = BASE_SEED) -> random.Random:
    return random.Random(table_seed(table_name, base_seed))
