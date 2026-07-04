# Credentials from zero (GEE + Copernicus CDS)

Step-by-step for someone who has **never** used the Google Cloud console or
had a Copernicus account. Each takes ~10 minutes in a browser plus two
commands in a terminal, and is done **once per user** — after that every
run just works.

| Provider | Needed for | You end up with |
| --- | --- | --- |
| Google Earth Engine (GEE) | `sentinel/script1`, future MODIS driver | a free EE-registered Cloud project + `~/.config/earthengine/credentials` |
| Copernicus CDS | AgERA5 climate, SEAS5 forecasts | a Personal Access Token in `~/.cdsapirc` |

---

## 1. Google Earth Engine

You do **not** need to open the Google Cloud console at any point — the
Earth Engine registration wizard creates the Cloud project for you.

### 1a. Register (browser, once)

1. Have a Google account (any Gmail works).
2. Open <https://code.earthengine.google.com/register> and sign in.
3. Choose **"Register a Noncommercial or Commercial Cloud project"**.
4. Pick **Unpaid usage** (research/academic use is free), then a project
   type such as **Academia & Research**.
5. When asked for a Cloud project, choose **"Create a new Google Cloud
   Project"** right there in the wizard. Give it an ID like
   `ee-<yourname>` (lowercase, no spaces). If a yellow banner asks you to
   accept the *Google Cloud Terms of Service*, click the link, accept, and
   come back.
6. Click **Confirm**. **Write down the project ID** (e.g. `ee-lizeth`) —
   the scripts need it as `gee_project=`.

### 1b. Authenticate on the server (terminal, once)

```bash
# inside the agwise_data conda env
pip install earthengine-api
earthengine authenticate
```

CGLabs has no browser, so if nothing opens run:

```bash
earthengine authenticate --auth_mode=notebook
```

It prints a long URL → open it **on your laptop's browser** → sign in with
the same Google account → *Allow* → copy the verification code it shows →
paste it back in the terminal. That writes
`~/.config/earthengine/credentials` (treat it like a password: never
commit or share it).

### 1c. Verify (10 seconds)

```bash
python -c "import ee; ee.Initialize(project='ee-<yourname>'); print(ee.Number(1).add(1).getInfo())"
```

Prints `2` → you are done. Use the same ID in the phenology scripts:
`gee_project="ee-<yourname>"`.

### GEE troubleshooting

- `ee.Initialize: no project found` → you must pass
  `project="ee-<yourname>"`; there is no default.
- `Not signed up for Earth Engine` → step 1a was not finished for the
  account you authenticated with; redo the registration with that account.
- `Permission denied / quota project` → the Cloud project exists but is not
  EE-registered: go back to the register URL and attach it as *Unpaid
  usage*.

---

## 2. Copernicus CDS (AgERA5 + SEAS5)

### 2a. Create the account (browser, once)

1. Open <https://cds.climate.copernicus.eu> → **Login** (top right) →
   **Register** (accounts are managed by ECMWF; the sign-in page has a
   create-account link).
2. Fill in name/email/password, accept the terms, then click the
   activation link they email you.

### 2b. Get your token

1. Log in at <https://cds.climate.copernicus.eu>.
2. Click your name (top right) → **Your profile**.
3. Copy the **Personal Access Token** shown there.

### 2c. Put it in `~/.cdsapirc`

```bash
cat > ~/.cdsapirc <<'EOF'
url: https://cds.climate.copernicus.eu/api
key: <paste-your-token-here>
EOF
chmod 600 ~/.cdsapirc
```

⚠️ **Never paste the token inside a script or notebook** — a leaked key
has to be rotated (profile page → regenerate).

### 2d. Accept the dataset licences (one click each, once)

Downloads fail with a licence error until you accept the terms **on each
dataset's page**: open the dataset → **Download** tab → scroll to *Terms of
use* → **Accept**. For this package:

- [Agrometeorological indicators (AgERA5)](https://cds.climate.copernicus.eu/datasets/sis-agrometeorological-indicators)
- [Seasonal forecast daily data on single levels (SEAS5)](https://cds.climate.copernicus.eu/datasets/seasonal-original-single-levels)

### 2e. Verify (small real download)

```bash
agwise-data get --vars TMAX --country Rwanda --years 2023:2023 --freq monthly
```

### CDS troubleshooting

- `403 ... required licences not accepted` → step 2d for that dataset.
- `401/Authentication failed` → token pasted wrong, or `~/.cdsapirc` still
  has the old `url: .../api/v2` from the legacy CDS — the new API url has
  no `/v2`.
- Request sits in *queued* for a long time → normal on CDS, especially
  SEAS5; the driver waits and caches the result so it is paid only once.

CHIRPS, SoilGrids and the Copernicus DEM need **no credentials at all**.
