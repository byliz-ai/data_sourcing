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

### 1b. Authenticate — do it on your LAPTOP, then copy one file

**Do not run `earthengine authenticate` on CGLabs.** Google blocks OAuth
sign-ins where the browser is on a different machine than the one running
the command ("Access blocked … for your security"), so the server-side
flows fail. The reliable path: authenticate on a machine that *has* a
browser, then copy the resulting credentials file (a portable refresh
token) to the server.

**On your laptop** (any OS; needs Python — Anaconda/Miniconda is fine):

```bash
pip install --upgrade earthengine-api   # --upgrade matters: old versions
                                        # use retired flows Google now blocks
earthengine authenticate
```

Your normal browser opens → sign in with the Google account from step 1a →
*Allow*. Done — it wrote a small file called `credentials`:

- Linux/Mac: `~/.config/earthengine/credentials`
- Windows: `C:\Users\<you>\.config\earthengine\credentials`

**Copy that file to CGLabs**: in JupyterLab, drag-and-drop it into the
file browser (it lands in your home dir), then in a terminal:

```bash
mkdir -p ~/.config/earthengine
mv ~/credentials ~/.config/earthengine/credentials
chmod 600 ~/.config/earthengine/credentials
```

Treat the file like a password — never commit or share it. On CGLabs also
install the client once: `pip install earthengine-api` (inside the
`agwise_data` env).

**No Python on the laptop?** Use Google Colab as the "browser machine":
open <https://colab.research.google.com> → new notebook → run
`import ee; ee.Authenticate(auth_mode='notebook')` and follow the link
(this Notebook Authenticator flow is Google-hosted, so it is not blocked
there), then export the file with
`from google.colab import files; files.download('/root/.config/earthengine/credentials')`
and upload it to CGLabs as above.

**For unattended/scheduled jobs** (no human to sign in, credentials shared
by a service, token expired revocation worries): the clean long-term
answer is a [service account](https://developers.google.com/earth-engine/guides/service_account)
— create it under your EE-registered project, download its JSON key, and
initialize with `ee.ServiceAccountCredentials(email, keyfile)`. More
clicks in the Cloud console, so start with the credentials-file copy and
move to this only when needed.

### 1c. Verify (10 seconds)

```bash
python -c "import ee; ee.Initialize(project='ee-<yourname>'); print(ee.Number(1).add(1).getInfo())"
```

Prints `2` → you are done. Use the same ID in the phenology scripts:
`gee_project="ee-<yourname>"`.

### GEE troubleshooting

- **"Access blocked: … for your security" / "Google ha bloqueado el
  acceso"** during sign-in → you are running the auth command on a
  machine without a browser (CGLabs), or your `earthengine-api` is old
  and uses a retired OAuth flow. Fix: `pip install --upgrade
  earthengine-api` **on your laptop**, authenticate there, copy the
  credentials file over (section 1b) — never authenticate on the server.
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
