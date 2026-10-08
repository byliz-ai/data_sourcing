# agwise-data vs. prismpy — análisis comparativo y plan de adopción

*Fecha del análisis: 2026-10-07 · agwise-data v0.32.2 · prismpy v0.1.0
(commit `cfe0219`, <https://github.com/izuku-franck1555/prismpy>). prismpy se
revisó leyendo el código; no se ejecutó.*

**Conclusión:** prismpy **no reemplaza** a agwise-data; son complementarios.
Nosotros somos la capa *fetch → harmonize → cache*; prismpy es la capa
*datos → paquete listo para modelo* (CRAFT, PYTHIA/DSSAT, ACEA/AquaCrop,
SARRA-Py). Está muy acoplado a su app web (prismweb), así que no conviene
adoptarlo como dependencia. Lo valioso son sus **patrones de calidad,
trazabilidad y reproducibilidad**, justo donde somos más débiles.

## 1. Comparación

| Tema | **agwise-data** (v0.32.2, ~10k líneas) | **prismpy** (v0.1.0 alpha, ~70k líneas src, ~3.300 tests) |
|---|---|---|
| Clima | CHIRPS v2/v3, AgERA5, SEAS5 + QDM | NASA POWER (solo puntos), TAMSAT, AgERA5, ISIMIP3b (proyecciones) |
| Suelo | SoilGrids (6 prof.), iSDA local | HWSD v2 por capas, eGHR, iSDA (S3). **Sin SoilGrids** |
| Otros | DEM + terreno, MODIS NDVI/EVI + suavizado, WorldCover, geoBoundaries | SPAM (vintages + digest), GADM local, GAEZ, Köppen, ECOCROP |
| Salidas | DSSAT, APSIM, WOFOST, ORYZA + cubos/puntos | CRAFT, PYTHIA, ACEA, SARRA-Py |
| Interfaz | Python + R + CLI, catálogo STAC, caché compartida | CLI con config YAML base + "DOME", ICASA/ACE |
| **Calidad de datos** | Mínima (swap TMIN/TMAX, negativos CHIRPS, años incompletos) | **Muy fuerte**: rangos físico/plausible, validación post-escritura, "no silent zero rain" |
| **Trazabilidad** | `.meta.json` por año, sin hashes/versión/cadena de transformaciones | **Muy fuerte**: `ProvenanceTracker`, manifest determinista con SHA256, declaración de suelo en el artefacto |
| Tests | ~250 sin red, CI py3.10/3.12 | unit/integration + tests "estructurales" (AST) y byte-pins |

**Debilidades de prismpy (no copiar):**
- Agregados por zona Köppen **sintéticos** (`koppen/data/zone_aggregates_v1.json`:
  "do NOT calibrate").
- Saxton-Rawls simplificado (`models/soil.py:61`: sin correcciones de 2º paso,
  sin OC→OM). El nuestro (`writers/soil.py:51-81`) es más fiel.
- Lógica concentrada en `pipeline/executor.py` (4.739 líneas); acoplado a prismweb.

## 2. Defectos propios detectados

1. **Clave de caché de productos sin fuente** (`src/agwise_data/api.py:489`, y
   stems análogos en :912, 1118, 1305, 1447, 1752, 2516): `Daily_PRCP_2015_2024`
   es igual para `chirps` y `chirps_v3` → se devuelve el producto equivocado.
   Listas de años no contiguas también colisionan con el rango completo.
2. **DSSAT `.WTH`** escribe `nan` en lugar de `-99` en días con faltantes parciales.
3. **DSSAT `.SOL`**: SLCF siempre `-99` aunque CFVO está disponible
   (`writers/soil.py:350`).
4. **Viento**: AgERA5 es a 10 m y pasa a WOFOST/ORYZA sin convertir a 2 m.
5. **Sin reintentos** en SoilGrids WCS, GEE `computePixels`, HTTP
   (`cache.download_file`) ni geoBoundaries; solo CDS tiene backoff.

## 3. Plan de adopción

### Fase 0 — Correcciones (≈1 semana)
*Hecha en v0.33.0.*
- Los 5 defectos anteriores, cada uno con su test.
- Helper único `retry_with_backoff` inspirado en prismpy
  `sources/common/retry.py:26` (backoff exponencial + jitter, solo errores
  transitorios), aplicado a todos los drivers.

### Fase 1 — Control de calidad (≈2–3 semanas) · *mayor valor*
*En curso. v0.34.0: rangos de dos niveles con defaults generales
(`qc_ranges.yaml`), `qc=`/`qc_ranges=` y `<producto>.qc.json` en
`get_climate`/`get_static`. v0.35.0: QC también en `get_seasonal`,
`get_season` y `extract_*`; lluvia faltante nunca cuenta como 0; nodata de
rasters enteros enmascarado antes de escalar.*
- **Rangos de dos niveles** (prismpy `validators/scientific.py:119`): *físico*
  → defecto/rechazo; *plausible* → advertencia. Declarados por variable en el
  catálogo YAML (campo `valid_range` existente), aplicados en
  `harmonize.standardize`.
- **Consistencia entre variables**: TMAX<TMIN, lluvia negativa, SRAD > máximo
  teórico, suma de textura ≠100 (renormalizar ≤3 %, advertir ≤5 %, excluir >5 %;
  prismpy `harmonize/texture_renormalize.py`).
- **Gate de nodata antes de escalar** (evita 255 → pH 25.5; prismpy
  `pipeline/executor.py:1879-1900`). Revisar drivers iSDA y SoilGrids.
- **Nunca rellenar lluvia con 0**: faltante = NaN/`-99`, con test que lo garantice.
- **Gap-fill de clima** solo para huecos ≤5 días en SRAD/TMIN/TMAX, nunca en
  lluvia, registrando lo rellenado (prismpy `sources/climate/_gapfill.py`).
- **Validación post-escritura**: releer .WTH/.SOL/.met y validar formato,
  rangos y continuidad de fechas (base: prismpy
  `translators/_shared/dssat_sol_validator.py:125`).
- Entregable: `qc_report.json` junto a cada producto.

### Fase 2 — Trazabilidad y reproducibilidad (≈2 semanas)
- `.meta.json` ampliado: SHA256 del archivo, versión de `agwise_data`,
  `catalog_version` (incluirla también en la clave de caché) y lista de
  transformaciones aplicadas (unidades, recorte, regrid, gap-fill, bias-correct).
- **Manifest por ejecución** en cada `out_dir` de los writers: archivos +
  SHA256, fuentes, parámetros, versión, advertencias. Determinista (claves
  ordenadas, sin timestamps) — prismpy `packaging/manifest.py`.
- **Declaración en el artefacto**: línea `!` en .SOL/.WTH indicando fuente y
  versión (DSSAT la ignora) — prismpy `packaging/soil_declaration.py`.
- **Texto de métodos automático**: párrafo con fuentes, versiones, citas y
  transformaciones, generado desde las citas del catálogo.

### Fase 3 — Nuevas capacidades (priorizar con el equipo)

| Prioridad | Qué | Por qué |
|---|---|---|
| Alta | **SPAM 2020** como máscara por cultivo, con vintage registrado y content digest (prismpy `sources/crop_areas/spam_vintage.py`) | Complementa WorldCover con máscara específica por cultivo |
| Alta | **Extracción en puntos bilineal/IDW** (hoy solo vecino más cercano, `api.py:572-578`) | Mejor extracción en ensayos; IDW de prismpy es una función pura (`harmonize/idw_interpolation.py:130`) |
| Media | **NASA POWER** como fuente alternativa (RH2M, WS2M a 2 m, punto de rocío) | Respaldo cuando falla CDS; sin cuenta |
| Media | **HWSD v2** como respaldo de suelo, perfil por capas (prismpy `sources/soil/hwsd.py:662-745`) | Fuera de África o si falla SoilGrids |
| Media | **ISIMIP3b** + tabla CO₂ por SSP (prismpy `data_sources/isimip3b.py`, `standards/co2_ppm.py`) | Escenarios de cambio climático |
| Baja | Alias ICASA en `harmonize` + export ACE/AgMIP | Interoperabilidad AgMIP, sin abandonar `AGRO.*` |
| Baja | TAMSAT | Alternativa a CHIRPS en África |

### Fase 4 — Tests y CI (en paralelo)
- Pocos **tests estructurales** de alto valor: no-zero-rain, todos los drivers
  usan el helper de retry, todo `xr.open_dataset` se cierra.
- **Byte-pin** de writers DSSAT/APSIM (fixture con hash, como prismpy
  `tests/structural/test_dssat_sol_writer_byte_pin.py`).
- Límites superiores de versión en dependencias críticas (numpy, xarray, rasterio).

## 4. Qué no adoptar
- Cockpit / wizard / flujo de overrides (dependen de prismweb).
- Config base + DOME: nuestra API por función es más simple para usuarios R/Python.
- Köppen/ECOCROP hasta que tengan datos reales.
- El volumen de tests "sprint-pinned": tomar el patrón, no la cantidad.

## 5. Siguiente paso
Empezar por la **Fase 0** (el defecto de clave de caché puede estar
devolviendo datos de la fuente equivocada) y seguir con la **Fase 1**.
