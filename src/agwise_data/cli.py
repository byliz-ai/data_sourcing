"""Command-line interface.

The CLI is the language-agnostic entry point: the R wrapper (r/agwise_data.R)
and any shell/cron job call it and parse the JSON line it prints last.

Examples::

    agwise-data get --vars PRCP,TMAX --country Kenya --years 2015:2024 \
        --freq monthly --format nc,tif
    agwise-data extract --points trials.csv --vars PRCP,TMAX \
        --planting-col Pl_date --harvest-col Hv_date --out trials_climate.csv
    agwise-data catalog list
    agwise-data catalog stac chirps
    agwise-data cache info
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _parse_years(text: str):
    """'2015:2024' → range; '2020' → [2020]; '2018,2020' → list."""
    text = text.strip()
    if ":" in text:
        a, b = text.split(":", 1)
        return list(range(int(a), int(b) + 1))
    if "," in text:
        return [int(y) for y in text.split(",") if y.strip()]
    return [int(text)]


def _emit(payload: dict) -> None:
    print(json.dumps(payload, default=str))


def _add_region_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--country", help="Country name or ISO3 code")
    p.add_argument("--admin-level", type=int, default=0, dest="admin_level")
    p.add_argument("--admin-name", dest="admin_name", help="Admin unit name (needs --admin-level)")
    p.add_argument(
        "--bbox",
        help="west,south,east,north (alternative to --country)",
    )


def cmd_get(args) -> dict:
    from .api import get_climate

    bbox = [float(v) for v in args.bbox.split(",")] if args.bbox else None
    results = get_climate(
        variables=args.vars,
        years=_parse_years(args.years),
        country=args.country,
        bbox=bbox,
        admin_level=args.admin_level,
        admin_name=args.admin_name,
        freq=args.freq,
        source=args.source,
        domain=args.domain,
        out_format=[f.strip() for f in args.format.split(",")],
        out_dir=Path(args.out_dir) if args.out_dir else None,
        overwrite=args.overwrite,
    )
    return {
        "ok": True,
        "outputs": [
            {
                "variable": var,
                "short": info["short"],
                "source": info["source"],
                "nc": str(info["nc"]) if info["nc"] else None,
                "tif": str(info["tif"]) if info["tif"] else None,
            }
            for var, info in results.items()
        ],
    }


def cmd_extract(args) -> dict:
    from .api import extract_growing_season, extract_points

    out_path = Path(args.out)
    if args.planting_col and args.harvest_col:
        df = extract_growing_season(
            points=args.points,
            variables=args.vars,
            planting_col=args.planting_col,
            harvest_col=args.harvest_col,
            legacy_names=not args.agwise_names,
            source=args.source,
            lon_col=args.lon_col,
            lat_col=args.lat_col,
        )
    elif args.start and args.end:
        df = extract_points(
            points=args.points,
            variables=args.vars,
            start=args.start,
            end=args.end,
            freq=args.freq,
            source=args.source,
            lon_col=args.lon_col,
            lat_col=args.lat_col,
        )
    else:
        raise SystemExit(
            "extract needs either --planting-col/--harvest-col (growing-season "
            "mode) or --start/--end (time-series mode)"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return {"ok": True, "outputs": [{"csv": str(out_path), "rows": len(df)}]}


def cmd_get_static(args) -> dict:
    from .api import get_static

    bbox = [float(v) for v in args.bbox.split(",")] if args.bbox else None
    depths = (
        [d.strip() for d in args.depths.split(",") if d.strip()]
        if args.depths
        else None
    )
    results = get_static(
        variables=args.vars,
        country=args.country,
        bbox=bbox,
        admin_level=args.admin_level,
        admin_name=args.admin_name,
        depths=depths,
        source=args.source,
        domain=args.domain,
        out_format=[f.strip() for f in args.format.split(",")],
        out_dir=Path(args.out_dir) if args.out_dir else None,
        overwrite=args.overwrite,
    )
    return {
        "ok": True,
        "outputs": [
            {
                "variable": var,
                "short": info["short"],
                "source": info["source"],
                "nc": str(info["nc"]) if info["nc"] else None,
                "tif": str(info["tif"]) if info["tif"] else None,
            }
            for var, info in results.items()
        ],
    }


def cmd_extract_static(args) -> dict:
    from .api import extract_static_points

    depths = (
        [d.strip() for d in args.depths.split(",") if d.strip()]
        if args.depths
        else None
    )
    df = extract_static_points(
        points=args.points,
        variables=args.vars,
        depths=depths,
        source=args.source,
        lon_col=args.lon_col,
        lat_col=args.lat_col,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return {"ok": True, "outputs": [{"csv": str(out_path), "rows": len(df)}]}


def cmd_catalog(args) -> dict:
    from . import catalog
    from .stac import to_stac_collection

    if args.action == "list":
        return {"ok": True, "sources": catalog.list_sources()}
    if not args.source_id:
        raise SystemExit(f"catalog {args.action} needs a source id")
    if args.action == "show":
        return {"ok": True, "entry": catalog.get_entry(args.source_id)}
    if args.action == "stac":
        return {"ok": True, "collection": to_stac_collection(args.source_id)}
    raise SystemExit(f"Unknown catalog action '{args.action}'")


def cmd_cache(args) -> dict:
    from .config import Config

    config = Config.load()
    if args.action == "path":
        return {"ok": True, "root": str(config.root)}
    files = sorted(
        p
        for p in config.root.rglob("*")
        if p.is_file() and not p.name.endswith((".meta.json", ".lock", ".tmp"))
    )
    if args.action == "ls":
        return {
            "ok": True,
            "root": str(config.root),
            "files": [str(p.relative_to(config.root)) for p in files],
        }
    if args.action == "info":
        total = sum(p.stat().st_size for p in files)
        return {
            "ok": True,
            "root": str(config.root),
            "n_files": len(files),
            "total_gb": round(total / 1e9, 2),
        }
    raise SystemExit(f"Unknown cache action '{args.action}'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agwise-data",
        description="AgWise data access layer: fetch, harmonize and cache climate data.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="Fetch a harmonized climate cube for a region")
    p_get.add_argument("--vars", required=True, help="e.g. PRCP,TMAX or AGRO.PRCP")
    p_get.add_argument("--years", required=True, help="e.g. 2015:2024")
    _add_region_args(p_get)
    p_get.add_argument("--freq", choices=["daily", "monthly"], default="monthly")
    p_get.add_argument("--format", default="nc", help="nc, tif or nc,tif")
    p_get.add_argument("--source", help="Override the default source for the variables")
    p_get.add_argument("--domain", help="Cache domain (default: auto)")
    p_get.add_argument("--out-dir", dest="out_dir")
    p_get.add_argument("--overwrite", action="store_true")
    p_get.set_defaults(func=cmd_get)

    p_ex = sub.add_parser("extract", help="Extract values at point locations")
    p_ex.add_argument("--points", required=True, help="CSV with lon/lat columns")
    p_ex.add_argument("--vars", required=True)
    p_ex.add_argument("--out", required=True, help="Output CSV path")
    p_ex.add_argument("--planting-col", dest="planting_col")
    p_ex.add_argument("--harvest-col", dest="harvest_col")
    p_ex.add_argument("--start", help="ISO date (time-series mode)")
    p_ex.add_argument("--end", help="ISO date (time-series mode)")
    p_ex.add_argument("--freq", choices=["daily", "monthly"], default="daily")
    p_ex.add_argument("--lon-col", dest="lon_col")
    p_ex.add_argument("--lat-col", dest="lat_col")
    p_ex.add_argument(
        "--agwise-names",
        action="store_true",
        help="Use PRCP_m1 style columns instead of legacy Precipitation_m1",
    )
    p_ex.add_argument("--source")
    p_ex.set_defaults(func=cmd_extract)

    p_gs = sub.add_parser(
        "get-static", help="Fetch harmonized static layers (soil, DEM) for a region"
    )
    p_gs.add_argument(
        "--vars", required=True, help="e.g. ELEV,SLOPE or CLAY,PH or SOIL.CLAY"
    )
    _add_region_args(p_gs)
    p_gs.add_argument("--depths", help="e.g. 0-5cm,5-15cm (soil layers only)")
    p_gs.add_argument("--format", default="nc", help="nc, tif or nc,tif")
    p_gs.add_argument("--source", help="Override the default source for the variables")
    p_gs.add_argument("--domain", help="Cache domain (default: auto)")
    p_gs.add_argument("--out-dir", dest="out_dir")
    p_gs.add_argument("--overwrite", action="store_true")
    p_gs.set_defaults(func=cmd_get_static)

    p_es = sub.add_parser(
        "extract-static", help="Extract soil/topography values at point locations"
    )
    p_es.add_argument("--points", required=True, help="CSV with lon/lat columns")
    p_es.add_argument("--vars", required=True)
    p_es.add_argument("--out", required=True, help="Output CSV path")
    p_es.add_argument("--depths", help="e.g. 0-5cm,5-15cm (soil layers only)")
    p_es.add_argument("--lon-col", dest="lon_col")
    p_es.add_argument("--lat-col", dest="lat_col")
    p_es.add_argument("--source")
    p_es.set_defaults(func=cmd_extract_static)

    p_cat = sub.add_parser("catalog", help="Inspect the dataset catalog")
    p_cat.add_argument("action", choices=["list", "show", "stac"])
    p_cat.add_argument("source_id", nargs="?")
    p_cat.set_defaults(func=cmd_catalog)

    p_cache = sub.add_parser("cache", help="Inspect the shared cache")
    p_cache.add_argument("action", choices=["path", "ls", "info"])
    p_cache.set_defaults(func=cmd_cache)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        payload = args.func(args)
    except Exception as exc:  # let the JSON line carry the failure too
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
