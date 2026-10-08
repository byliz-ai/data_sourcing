# Onboarding — AgWise data-sourcing (new teammates start here)

The **data-sourcing layer**: one call to fetch, harmonize and cache the climate,
soil, terrain and remote-sensing data every AgWise module needs — and to turn it
into analysis-ready crop-model inputs — with a **shared cache** so a dataset is
downloaded once and reused by everyone.

**Mental model:** on CGLabs the code and the environment are **installed once and
shared**. You don't clone and you don't install — you *activate* the shared env
and use it. Repo changes are made in the one shared clone; your experiments and
outputs go in your own scratch folder.

> Read this page once, then follow the docs map in §4. This page replaces the
> old `claude.ai/claude-code/onboard/…` link, which is no longer open.

---

## 0. Prerequisite — access to the shared space (do this FIRST)

Everything below assumes `/home/jovyan/agwise-datasourcing` exists in your
container. It is **not a folder** — it is the CGLabs shared space
`AgWise_dataSourcing`, mounted over NFS, and CGLabs mounts it only for accounts
that are **members of that shared space**. Check:

```bash
mountpoint -q /home/jovyan/agwise-datasourcing \
  && echo "OK — shared space mounted" \
  || echo "MISSING — request access (see below)"
```

If it says MISSING, nothing is broken and there is nothing to fix from inside
the container — ask the CGLabs admins to:

> add my account to the CGLabs shared space **`AgWise_dataSourcing`**
> (mounted at `/home/jovyan/agwise-datasourcing`).

Then **stop and start your server** from the Hub control panel (*File → Hub
Control Panel → Stop My Server → Start My Server*). Mounts are applied when the
container starts — reloading the browser tab is not enough. This is the single
most common reason for "they gave me access but I still don't see it".

⚠️ **Do not work around it** by cloning the repo into
`~/agwise-datasourcing/code/data_sourcing` yourself. When the real shared space
is mounted it will hide that folder: the copy becomes invisible, keeps consuming
your home quota, and the mismatch is confusing to debug later. Delete any such
clone before requesting the mount. Building a personal `agwise_data` conda env is
also a false shortcut — a personal env of that name **wins by name** over the
shared one once the mount arrives (see README §2.2), and without `Landing` you
have no staged data, so every variable re-downloads against your own quota.

(`~/common_data` is a *different* shared space — `shared-volume` — that everyone
gets, so seeing it says nothing about your `AgWise_dataSourcing` access.)

---

## Where everything lives (CGLabs)

| Thing | Path |
| --- | --- |
| Shared repo clone (**all git + edits**) | `/home/jovyan/agwise-datasourcing/code/data_sourcing` |
| Shared conda env | `/home/jovyan/agwise-datasourcing/envs/agwise_data` |
| Raw inputs (read-only) | `…/Global_GeoData/Landing` — auto-default |
| Shared download cache (read/write) | `…/Global_GeoData/Processed` — auto-default |
| Your scratch / test outputs | a folder you own — **never** write into `Landing`/`Processed` |

---

## 1. Get running (first 5 minutes)

Everything is already installed — you only register the shared env once, then
activate it each session:

```bash
# Once per user:
conda config --append envs_dirs /home/jovyan/agwise-datasourcing/envs
# Every session:
conda activate agwise_data          # puts the `agwise-data` command on your PATH
```

Full details, and the off-CGLabs / new-server install, are in **README §2.2**.
On CGLabs there is **nothing else to configure** — the shared data folders are
the defaults, so you reuse already-downloaded data out of the box.

Then, only for the sources that need an account (Copernicus CDS, Google Earth
Engine), set your **own** credentials — see **docs/credentials_setup.md**. Soil,
terrain and admin boundaries need no account, so you can get a first result with
zero credentials (README §2.4 "First success").

> **Golden rule:** *data is shared, credentials are personal.* Keep your tokens
> in your own home (`chmod 600`), never in the repo or a shared folder.

---

## 2. Using the layer

- **What functions exist, every parameter:** `REFERENCE.md`.
- **Which function for which task (area / dataset / period / output), in
  Python · R · CLI · AI assistant:** `docs/user_guide.md`.
- **Just ask in plain language, with the AI assistant you prefer** (Claude
  Code, Codex, Gemini CLI, Copilot, Cursor, ChatGPT…). Tell it first: *"Read
  /home/jovyan/agwise-datasourcing/code/data_sourcing/AGENTS.md"*, then describe
  the task. It picks the function and runs it, or writes the code for you to
  run (`docs/user_guide.md` §5.3).
- **Runnable quickstarts:** `examples/quickstart.py` / `examples/quickstart.R`.
- The public API is 23 functions (`get_climate`, `extract_points`,
  `get_soil`/`get_dem`, `get_seasonal`, `get_modis`, `to_dssat`/`to_apsim`/
  `to_wofost`/`to_oryza`, `bias_correct`, …), each with an `ad_*` R wrapper and a
  CLI subcommand.
- **Data quality is checked for you** (since v0.34): impossible values become
  missing, unusual ones trigger a `QCWarning`, and every crop-model run writes a
  `qc_report.json`. When something looks off, read `docs/quality_control.md`.
- **Every run is traceable** (since v0.37): crop-model runs also write
  `manifest.json` (files + checksums, sources, parameters) and `METHODS.md`
  (a paragraph with citations, ready for a report) — `docs/provenance.md`.
- **Using an AI assistant?** `AGENTS.md` in the repo is written for any of
  them. Most terminal agents load it on their own when started inside the
  repo folder. Elsewhere, or in a browser chat, give it the path or attach the
  file.

---

## 3. Working on the code (contributors)

- **Make and commit repo changes ONLY from the shared clone**
  `/home/jovyan/agwise-datasourcing/code/data_sourcing`. Don't keep a second
  working copy — your own scratch dir is for test scripts and outputs only,
  never a git clone.
- Trunk-based: commit and **push straight to `origin/main`**; CI runs on every
  push. If an AI assistant wrote part of the change, say so in the commit
  body (e.g. the `Co-Authored-By:` line your tool is configured to add).
- **Editable install:** a single `git pull` in the shared clone updates everyone
  at once. After a version bump, re-run `pip install -e` once to refresh
  `agwise_data.__version__`.
- **Tests** (network-free, no credentials):
  `"/home/jovyan/agwise-datasourcing/envs/agwise_data/bin/python" -m pytest -q -o faulthandler_timeout=60`
  from the shared clone. One MODIS GeoTIFF test can fail on a local GDAL quirk
  but passes in CI; everything else must stay green. Note the R wrappers
  (`r/agwise_data.R`) are **not** covered by pytest — changes there need a live
  R run.
- **git identity:** if a clone errors *"Author identity unknown"*, set it once in
  that clone: `git config user.name "…" && git config user.email "…"`.
- Conventions (adding a source, the "one home per topic" doc rule): `CONTRIBUTING.md`.

---

## 4. Docs map

Read in order the first time: **README** (how it works · install/activate ·
first success) → **docs/credentials_setup.md** (CDS + Earth Engine, click by
click) → **docs/cglabs_setup.md** (shared-server ops: from-scratch install,
data roots, R, performance, the ~32 GB container ceiling) → **docs/user_guide.md**
(workflow + interfaces) → **REFERENCE.md** (function reference) →
**docs/quality_control.md** (data quality) → **docs/provenance.md**
(manifests, citations) → **CONTRIBUTING.md**.

---

## Good to know

- **Don't reinstall** — the env is shared; reinstalling per-user defeats the point.
- **Shared disks are read-only inputs** (`Landing`, `Processed`, `common_data`,
  other agwise-* repos). Point `AGWISE_DATA_ROOT` at your own cache when testing.
- **~32 GB memory ceiling** on the CGLabs container (not the RAM `free` shows) —
  the layer sizes itself to it; run heavy bulk jobs sequentially.
- **Rainfall on CGLabs comes from the staged CHIRPS v3** (1981–2025, read
  locally from `Landing`, no account needed). Earth Engine is only needed for
  MODIS / crop-mask work, and for CHIRPS v2 in years outside that range — the
  UCSB host is 403-blocked, so v2 routes through Earth Engine.
