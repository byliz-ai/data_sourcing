# Credentials setup (GEE + Copernicus CDS)

Everything here is done **once per user**. Pick your path:

| Your situation | Go to |
| --- | --- |
| I already have a CDS token and a GEE credentials file | [Path A — I have everything (≈5 min)](#path-a--i-have-everything-5-min) |
| I have never used Google Earth Engine or Copernicus | [Path B — first time, from zero (≈20 min)](#path-b--first-time-from-zero-20-min) |
| Something failed | [Troubleshooting](#troubleshooting) |

What each credential unlocks:

| Provider | Needed for | What you place on the server |
| --- | --- | --- |
| Copernicus CDS | AgERA5 climate, SEAS5 forecasts | token in `~/.cdsapirc` |
| Google Earth Engine (GEE) | `sentinel/script1`, future MODIS | file `~/.config/earthengine/credentials` |
| CHIRPS, SoilGrids, DEM | nothing — no account needed | — |

---

## Path A — I have everything (≈5 min)

You already have: a **CDS Personal Access Token**, a **GEE credentials
file** (from `earthengine authenticate` on any machine) and your **GEE
project ID** (looks like `ee-yourname`).

### A1. CDS token → `~/.cdsapirc`

In a CGLabs terminal (replace the token):

```bash
cat > ~/.cdsapirc <<'EOF'
url: https://cds.climate.copernicus.eu/api
key: <paste-your-token-here>
EOF
chmod 600 ~/.cdsapirc
```

### A2. GEE credentials file → `~/.config/earthengine/`

1. In JupyterLab, drag your `credentials` file into the file browser
   (it lands in your home directory).
2. In a terminal:

```bash
mkdir -p ~/.config/earthengine
mv ~/credentials ~/.config/earthengine/credentials
chmod 600 ~/.config/earthengine/credentials
pip install earthengine-api    # inside the agwise_data env, once
```

### A3. Verify both (30 seconds)

```bash
# CDS — small real download:
agwise-data get --vars TMAX --country Rwanda --years 2023:2023 --freq monthly

# GEE — must print 2:
python -c "import ee; ee.Initialize(project='ee-<yourname>'); print(ee.Number(1).add(1).getInfo())"
```

Both worked? You are done. Anything failed → [Troubleshooting](#troubleshooting).

---

## Path B — first time, from zero (≈20 min)

No prior accounts assumed. You will **never need to open the Google Cloud
console** — the Earth Engine wizard does it for you.

### B1. Copernicus CDS account (browser, ≈5 min)

1. Open <https://cds.climate.copernicus.eu> → **Login** (top right) →
   **Register** / create account.
2. Fill in email + password → click the **activation link** they email you.
3. Log in → click your **name** (top right) → **Your profile**.
4. Copy the **Personal Access Token** shown there.

### B2. Accept the dataset licences (browser, 2 clicks)

Downloads fail until you accept the terms of each dataset, once:

1. Open [Agrometeorological indicators (AgERA5)](https://cds.climate.copernicus.eu/datasets/sis-agrometeorological-indicators)
   → **Download** tab → scroll to *Terms of use* → **Accept**.
2. Same on [Seasonal forecast daily data (SEAS5)](https://cds.climate.copernicus.eu/datasets/seasonal-original-single-levels).

### B3. Google Earth Engine registration (browser, ≈5 min)

1. Have any Google account (Gmail).
2. Open <https://code.earthengine.google.com/register> and sign in.
3. Click **"Register a Noncommercial or Commercial Cloud project"**.
4. Choose **Unpaid usage** → **Academia & Research**.
5. Choose **"Create a new Google Cloud Project"** → give it an ID like
   `ee-yourname` (lowercase, no spaces).
   - If a yellow banner asks you to accept the *Cloud Terms of Service*:
     click its link, accept, come back, continue.
6. Click **Confirm**.
7. 📝 **Write down the project ID** (`ee-yourname`) — scripts need it.

### B4. GEE sign-in — on your LAPTOP, not on CGLabs

⚠️ Google **blocks** the sign-in if the browser is on a different machine
than the one running the command ("Access blocked … for your security").
So never run `earthengine authenticate` on CGLabs. Do it where a browser
exists, then copy one small file.

On your laptop (any OS with Python — Anaconda counts):

```bash
pip install --upgrade earthengine-api
earthengine authenticate
```

Your browser opens → sign in with the account from B3 → **Allow**. That
wrote the file you need:

- Windows: `C:\Users\<you>\.config\earthengine\credentials`
- Mac/Linux: `~/.config/earthengine/credentials`

No Python on the laptop? Use Colab as the browser machine:
<https://colab.research.google.com> → new notebook → run
`import ee; ee.Authenticate(auth_mode='notebook')` → follow the link →
then `from google.colab import files; files.download('/root/.config/earthengine/credentials')`.

### B5. Put both credentials on CGLabs

You now have a CDS token (B1) and a `credentials` file (B4) — follow
[Path A](#path-a--i-have-everything-5-min) steps A1 → A3. That's it.

---

## Troubleshooting

### GEE

- **"Access blocked / Google ha bloqueado el acceso"** while signing in →
  you ran the auth command on a machine without a browser (CGLabs), or
  your `earthengine-api` is old (retired OAuth flow). Fix:
  `pip install --upgrade earthengine-api` **on your laptop**, authenticate
  there, copy the file (B4–B5).
- `ee.Initialize: no project found` → pass `project="ee-yourname"`;
  there is no default.
- `Not signed up for Earth Engine` → registration (B3) was not finished
  for the account you signed in with; redo B3 with that account.
- `Permission denied / quota project` → the Cloud project is not
  EE-registered: reopen the register URL (B3) and attach it as *Unpaid
  usage*.
- Unattended/scheduled jobs with no human to sign in → long-term answer
  is a [service account](https://developers.google.com/earth-engine/guides/service_account)
  (`ee.ServiceAccountCredentials(email, keyfile)`); start with the
  file-copy approach and switch only when needed.

### CDS

- `403 … required licences not accepted` → step B2 for that dataset.
- `401 / Authentication failed` → token pasted wrong, or your
  `~/.cdsapirc` has the legacy `url: …/api/v2` — the current API url has
  **no** `/v2`.
- Request stuck in *queued* → normal on CDS (especially SEAS5); the
  driver waits and caches, so each request is paid only once.

⚠️ **Never paste tokens inside scripts or notebooks.** A leaked key must
be rotated (CDS: profile → regenerate; GEE: re-run `earthengine
authenticate`). Keep `~/.cdsapirc` and the credentials file `chmod 600`.
