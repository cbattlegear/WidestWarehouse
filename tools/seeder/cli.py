from __future__ import annotations

import argparse
from pathlib import Path

from .seed_emitter import generate_seed, TIME_GRAIN_SECONDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.seeder.cli")
    parser.add_argument("--model", default="model", help="Path to model root")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="Generate deterministic seed CSVs and SQL")
    gen.add_argument("--out", default="seed", help="CSV output root")
    gen.add_argument("--sql-out", default=str(Path("sql") / "95_seed"), help="Seed SQL output root")
    gen.add_argument("--scale", type=float, default=1.0, help="Starter seed scale factor")
    args = parser.parse_args(argv)

    if args.command == "generate":
        result = generate_seed(args.model, args.out, args.sql_out, args.scale)
        print("Schema   Rows")
        print("-------------")
        for schema in sorted(result.rows_by_schema):
            print(f"{schema:<7} {result.rows_by_schema[schema]:>10}")
        print("-------------")
        print(f"{'total':<7} {result.total_rows:>10}")
        print(f"elapsed {result.elapsed_seconds:.2f}s")
        print(f"TimeOfDay grain: {TIME_GRAIN_SECONDS // 60} minute(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
