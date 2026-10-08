"""Provenance: where every file came from and what was done to it.

Three layers, all built from the catalog (no hand-maintained lists):

* **Cache sidecars** (``<file>.meta.json``, written by
  :func:`agwise_data.cache.write_manifest`) get the package version, the
  file's SHA256 and the *recipe* of the source variable — a short digest of
  the catalog's version and per-variable recipe (source name, statistic,
  conversion, nodata). A cached file whose recipe no longer matches the
  catalog is rebuilt, so editing a conversion never reuses stale data.
* **Run manifests** (``<out_dir>/manifest.json``) for the crop-model writers:
  the files written with their SHA256, the sources and their citations, the
  parameters, the transformations and the QC summary. Deterministic — sorted
  keys, relative paths, no timestamps — so two identical runs produce
  byte-identical manifests and can be compared with ``diff``.
* **Methods text** (:func:`methods_text`, ``<out_dir>/METHODS.md``): a
  paragraph with sources, versions, transformations and full references,
  ready for a report or paper.

Adapted from prismpy's ``packaging/manifest.py`` and
``packaging/soil_declaration.py`` (see docs/prismpy_comparison.md §7).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

MANIFEST_NAME = "manifest.json"
METHODS_NAME = "METHODS.md"
_CHUNK = 8 * 1024 * 1024

# Transformations a crop-model run can apply, with the method citation they
# rest on. Keys are what a writer records; the text is what the manifest and
# the methods paragraph say.
TRANSFORMS: Dict[str, dict] = {
    "point_nearest": {
        "text": "values extracted at each point from the nearest grid cell",
    },
    "soil_fill": {
        "text": "a point on a masked soil cell takes the nearest valid cell "
                "(at least the 8 neighbouring cells)",
    },
    "qc_ranges": {
        "text": "daily values range-checked against physical limits "
                "(impossible values set to missing)",
    },
    "weather_checks": {
        "text": "days with TMIN > TMAX swapped; solar radiation above the "
                "extraterrestrial radiation set to missing",
        "ref": "fao56",
    },
    "gapfill": {
        "text": "gaps of up to {gapfill_days} days in TMAX, TMIN and SRAD "
                "linearly interpolated; rainfall never filled",
    },
    "texture_normalize": {
        "text": "clay, silt and sand rescaled to sum 100 %",
    },
    "saxton_rawls": {
        "text": "soil water limits and saturated conductivity from the Saxton "
                "and Rawls pedotransfer functions",
        "ref": "saxton_rawls",
    },
    "olsen_p": {
        "text": "Mehlich-3 extractable P converted to Olsen P",
    },
    "wind_2m": {
        "text": "10 m wind speed converted to 2 m",
        "ref": "fao56",
    },
    "vapour_pressure": {
        "text": "vapour pressure derived from relative humidity and mean "
                "temperature",
        "ref": "fao56",
    },
    "bias_correction": {
        "text": "forecast bias-corrected by quantile delta mapping",
        "ref": "qdm",
    },
}

METHOD_REFS = {
    "fao56": "Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). Crop "
             "evapotranspiration — Guidelines for computing crop water "
             "requirements. FAO Irrigation and Drainage Paper 56. FAO, Rome.",
    "saxton_rawls": "Saxton, K.E., Rawls, W.J. (2006). Soil water "
                    "characteristic estimates by texture and organic matter "
                    "for hydrologic solutions. Soil Sci. Soc. Am. J. 70, "
                    "1569–1578. https://doi.org/10.2136/sssaj2005.0117",
    "qdm": "Cannon, A.J., Sobie, S.R., Murdock, T.Q. (2015). Bias correction "
           "of GCM precipitation by quantile mapping: How well do methods "
           "preserve changes in quantiles and extremes? J. Climate 28, "
           "6938–6959. https://doi.org/10.1175/JCLI-D-14-00754.1",
}


# ---------------------------------------------------------------------------
def package_version() -> str:
    from . import __version__

    return __version__


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _entry(source_id: str) -> Optional[dict]:
    from . import catalog

    try:
        return catalog.get_entry(source_id)
    except KeyError:
        return None


def _spec(entry: dict, variable: str):
    """The catalog recipe block for ``variable`` (any namespace), or None."""
    from .harmonize import (
        canonical_name, rs_canonical_name, static_canonical_name,
        static_derived_from,
    )

    variables = entry.get("variables") or {}
    if variable in variables:
        return variables[variable]
    for resolve in (canonical_name, static_canonical_name, rs_canonical_name):
        try:
            name = resolve(variable)
        except (KeyError, ValueError):
            continue
        if name in variables:
            return variables[name]
        try:
            parent = static_derived_from(name)
        except KeyError:
            parent = None
        if parent and parent in variables:
            return {"derived_from": parent, **variables[parent]}
    return None


def recipe(source_id: str, variable: str) -> Optional[str]:
    """8-char digest of the catalog version + the variable's recipe.

    Changes whenever the catalog changes how this variable is read or
    converted (source name, statistic, conversion, nodata, dataset version),
    and only then — editing a description or a mirror URL does not
    invalidate the cache. None for a source not in the catalog.
    """
    entry = _entry(source_id)
    if entry is None:
        return None
    payload = {"version": entry.get("version"), "spec": _spec(entry, variable)}
    return hashlib.sha1(_canonical_json(payload).encode()).hexdigest()[:8]


def source_info(source_id: str) -> dict:
    """Title, version, license and citation of a catalog source."""
    entry = _entry(source_id) or {}
    info = {
        "title": entry.get("title", source_id),
        "version": entry.get("version"),
        "license": entry.get("license"),
        "citation": " ".join(str(entry.get("citation") or "").split()) or None,
    }
    return {k: v for k, v in info.items() if v is not None}


def variable_record(variable: str, source_id: str) -> dict:
    return {"source": source_id, "recipe": recipe(source_id, variable)}


# ---------------------------------------------------------------------------
# Cache sidecars
def product_transforms(meta: Mapping) -> List[str]:
    """Human-readable steps behind a cached file, derived from its sidecar."""
    steps = []
    src = meta.get("source_id") or meta.get("source")
    if src:
        steps.append(f"harmonized from {src} to canonical names and units "
                     "(catalog conversion)")
    if meta.get("domain_bbox"):
        steps.append(f"cropped to domain {meta.get('domain')} "
                     f"{list(meta['domain_bbox'])}")
    if meta.get("region"):
        steps.append(f"subset to region {meta['region']}")
    if meta.get("qc") and meta.get("qc") != "off":
        steps.append(f"range QC ({meta['qc']})")
    elif meta.get("qc") == "off":
        steps.append("range QC off")
    if meta.get("freq") == "monthly":
        steps.append("aggregated to monthly (missing days propagate)")
    if meta.get("smoothing"):
        steps.append(f"smoothed ({meta['smoothing'].get('method')})")
    if meta.get("cropmask"):
        steps.append("masked to cropland")
    if str(meta.get("method", "")).startswith("qdm"):
        steps.append(f"bias-corrected ({meta['method']})")
    if meta.get("derived_from"):
        steps.append(f"derived from {meta['derived_from']}")
    return steps


def enrich_sidecar(data_path: Path, meta: dict) -> dict:
    """Fields every ``.meta.json`` carries on top of the caller's ``meta``."""
    out = {"agwise_data_version": package_version()}
    variable = meta.get("variable")
    if variable:
        if meta.get("source_id"):
            r = recipe(meta["source_id"], variable)
            if r:
                out["recipe"] = r
        elif meta.get("source_ids"):
            out["recipes"] = {
                sid: recipe(sid, variable) for sid in meta["source_ids"]
            }
    steps = product_transforms(meta)
    if steps:
        out["transforms"] = steps
    path = Path(data_path)
    if path.is_file():
        out["sha256"] = sha256_file(path)
        out["bytes"] = path.stat().st_size
    return out


def recipe_current(meta: Mapping, source_id: str, variable: str) -> bool:
    """False only when a sidecar records a recipe that no longer matches.

    Files written before recipes were recorded carry none and stay valid.
    """
    old = meta.get("recipe")
    return old is None or old == recipe(source_id, variable)


# ---------------------------------------------------------------------------
# Point-frame provenance (DataFrame.attrs["provenance"])
def frame_provenance(frames: Iterable) -> Dict[str, dict]:
    """Merge the ``attrs["provenance"]`` of several DataFrames."""
    out: Dict[str, dict] = {}
    for df in frames:
        if df is None:
            continue
        out.update(getattr(df, "attrs", {}).get("provenance") or {})
    return out


def points_digest(df, lon_col: str, lat_col: str) -> dict:
    """Count and SHA256 of the point coordinates (rounded to 1e-6 deg)."""
    coords = "\n".join(
        f"{float(x):.6f},{float(y):.6f}"
        for x, y in zip(df[lon_col], df[lat_col])
    )
    return {"n": int(len(df)),
            "sha256": hashlib.sha256(coords.encode()).hexdigest()}


# ---------------------------------------------------------------------------
# Run manifests
def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _written_files(records: Sequence[Mapping], out_dir: Path) -> List[Path]:
    files = set()

    def add(v):
        if isinstance(v, (str, Path)):
            p = Path(v)
            if p.is_file():
                files.add(p.resolve())
        elif isinstance(v, (list, tuple)):
            for x in v:
                add(x)

    for rec in records:
        for key, val in rec.items():
            if key not in ("dir", "qc", "point"):
                add(val)
    root = out_dir.resolve()
    return sorted(files, key=lambda p: p.relative_to(root).as_posix()
                  if p.is_relative_to(root) else p.as_posix())


def _rel(path: Path, root: Path) -> str:
    path, root = path.resolve(), root.resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) \
        else path.as_posix()


def build_run_manifest(
    function: str,
    out_dir,
    records: Sequence[Mapping],
    variables: Mapping[str, dict],
    parameters: Optional[Mapping] = None,
    points: Optional[dict] = None,
    transforms: Sequence[str] = (),
    qc_summary: Optional[dict] = None,
    extra_files: Sequence = (),
) -> dict:
    """The deterministic ``manifest.json`` of one crop-model run."""
    out_dir = Path(out_dir)
    paths = _written_files(records, out_dir)
    paths += [Path(p).resolve() for p in extra_files if Path(p).is_file()]
    files = [
        {"path": _rel(p, out_dir), "sha256": sha256_file(p),
         "bytes": p.stat().st_size}
        for p in paths
    ]
    sources = sorted({v["source"] for v in variables.values()
                      if v.get("source") and v["source"] != "user-supplied"})
    params = dict(parameters or {})
    steps = [_transform_text(t, params) for t in transforms]
    refs = sorted({TRANSFORMS[t]["ref"] for t in transforms
                   if t in TRANSFORMS and "ref" in TRANSFORMS[t]})
    return _jsonable({
        "agwise_data_version": package_version(),
        "function": function,
        "parameters": params,
        "points": points,
        "variables": dict(sorted(variables.items())),
        "sources": {s: source_info(s) for s in sources},
        "transforms": steps,
        "method_references": [METHOD_REFS[r] for r in refs],
        "qc": qc_summary,
        "files": files,
    })


def _transform_text(key: str, params: Mapping) -> str:
    spec = TRANSFORMS.get(key)
    if spec is None:
        return key
    return spec["text"].format(gapfill_days=params.get("gapfill_days", 5))


def write_json(path, data: dict) -> Path:
    """Deterministic JSON (sorted keys, trailing newline), atomically."""
    from .cache import atomic_write

    path = Path(path)
    with atomic_write(path) as tmp:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True,
                                  ensure_ascii=False) + "\n")
    return path


def write_run(out_dir, manifest: dict) -> dict:
    """Write ``METHODS.md`` then ``manifest.json`` (which lists METHODS.md).

    Returns ``{"manifest": Path, "methods": Path}``.
    """
    out_dir = Path(out_dir)
    methods = out_dir / METHODS_NAME
    from .cache import atomic_write

    with atomic_write(methods) as tmp:
        tmp.write_text(methods_text(manifest) + "\n")
    manifest = dict(manifest)
    manifest["files"] = sorted(
        [f for f in manifest["files"] if f["path"] != METHODS_NAME]
        + [{"path": METHODS_NAME, "sha256": sha256_file(methods),
            "bytes": methods.stat().st_size}],
        key=lambda f: f["path"],
    )
    path = write_json(out_dir / MANIFEST_NAME, manifest)
    return {"manifest": path, "methods": methods}


# ---------------------------------------------------------------------------
# Declarations inside the written files
def declaration(
    variables: Mapping[str, dict], kinds: Sequence[str], prefix: str = "!",
    width: int = 78,
) -> List[str]:
    """Comment lines naming the package version and the sources used.

    ``kinds`` selects the variable namespaces to mention (``"AGRO"`` for a
    weather file, ``"SOIL"``/``"TOPO"`` for a soil file). Lines are kept
    under ``width`` characters and start with ``prefix`` (``!`` for DSSAT and
    APSIM, ``*`` for CABO), which the models skip.
    """
    by_source: Dict[str, List[str]] = {}
    for var, rec in sorted(variables.items()):
        if var.split(".")[0] not in kinds:
            continue
        src = rec.get("source") or "unknown"
        if src != "user-supplied":
            info = source_info(src)
            if info.get("version"):
                src = f"{src} v{info['version']}"
        short = var.split(".")[-1]
        by_source.setdefault(src, []).append("all" if short == "*" else short)
    parts = [f"{src} ({','.join(vs)})" for src, vs in sorted(by_source.items())]
    words = [f"agwise-data {package_version()};", "sources:"]
    words += [p + ";" for p in parts[:-1]] + parts[-1:]
    words.append("- see manifest.json")
    lines, cur = [], prefix
    for w in words:
        if len(cur) + 1 + len(w) > width and cur != prefix:
            lines.append(cur)
            cur = prefix
        cur = f"{cur} {w}"
    lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Methods text
def _var_label(var: str) -> str:
    from .harmonize import CANONICAL_VARS, STATIC_VARS

    meta = CANONICAL_VARS.get(var) or STATIC_VARS.get(var) or {}
    return meta.get("long_name") or var.split(".")[-1]


def methods_text(source) -> str:
    """A methods paragraph with sources, versions, steps and references.

    ``source`` is a run manifest (dict), a path to ``manifest.json`` or to a
    run ``out_dir``, or a cached product (``.nc``/``.tif``, whose sidecar is
    read).
    """
    manifest = _load_for_methods(source)
    version = manifest.get("agwise_data_version", "?")
    variables = manifest.get("variables") or {}
    by_source: Dict[str, List[str]] = {}
    for var, rec in sorted(variables.items()):
        by_source.setdefault(rec.get("source") or "unknown", []).append(
            _var_label(var))
    infos = manifest.get("sources") or {}

    what = manifest.get("function")
    lead = (f"Input data were prepared with agwise-data v{version}, the AgWise "
            "data-sourcing layer")
    lead += f" (`{what}`)." if what else "."
    sents = [lead]
    pts = manifest.get("points") or {}
    for src, labels in sorted(by_source.items()):
        if src == "user-supplied":
            sents.append(f"{_join(labels)} were supplied by the user.")
            continue
        info = infos.get(src) or source_info(src)
        title = info.get("title", src)
        ver = f" (version {info['version']})" if info.get("version") and \
            str(info["version"]) not in title else ""
        sents.append(f"{_join(labels)} came from {title}{ver}.")
    steps = list(manifest.get("transforms") or [])
    if pts.get("n"):
        sents.append(f"Data were prepared for {pts['n']} point(s).")
    if steps:
        sents.append("Processing: " + "; ".join(steps) + ".")
    qc = manifest.get("qc") or {}
    if qc:
        sents.append(_qc_sentence(qc))
    refs = [info.get("citation") for _, info in sorted(infos.items())
            if info.get("citation")]
    if not infos:
        refs = [source_info(s).get("citation") for s in sorted(by_source)
                if source_info(s).get("citation")]
    refs += list(manifest.get("method_references") or [])
    text = " ".join(s for s in sents if s)
    if refs:
        text += "\n\nReferences\n\n" + "\n".join(f"- {r}" for r in refs)
    return text


def _qc_sentence(qc: Mapping) -> str:
    files = qc.get("files")
    bad = qc.get("files_with_problems")
    if files is None:
        return ""
    return (f"All {files} written files were read back and validated"
            + (f"; {bad} reported problems (see qc_report.json)." if bad
               else " without problems."))


def _join(items: Sequence[str]) -> str:
    """'A, b and c' — sentence-initial capital, the rest lower-cased first letter."""
    items = [it[:1].lower() + it[1:] for it in items]
    text = items[0] if len(items) == 1 else \
        ", ".join(items[:-1]) + " and " + items[-1]
    return text[:1].upper() + text[1:]


def _load_for_methods(source) -> dict:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    if path.is_dir():
        path = path / MANIFEST_NAME
    if path.name == MANIFEST_NAME:
        return json.loads(path.read_text())
    from .cache import read_manifest

    meta = read_manifest(path)
    if not meta:
        raise FileNotFoundError(
            f"No provenance found for {path} (expected a run out_dir, a "
            "manifest.json or a cached product with a .meta.json sidecar)"
        )
    sources = meta.get("source_ids") or [meta.get("source_id") or
                                         meta.get("source")]
    variable = meta.get("variable", "?")
    return {
        "agwise_data_version": meta.get("agwise_data_version", "?"),
        "variables": {variable: {"source": sources[0]}} if len(sources) == 1
        else {f"{variable} [{s}]": {"source": s} for s in sources},
        "sources": {s: source_info(s) for s in sources if s},
        "transforms": meta.get("transforms")
        or product_transforms(meta),
    }
