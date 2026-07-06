# Credentials setup (GEE + Copernicus CDS)

Everything here is done **once per user**. Pick your path:

| Your situation | Go to |
| --- | --- |
| I already have a CDS token and a GEE credentials file | [Path A — I have everything (≈5 min)](#path-a--i-have-everything-5-min) |
| I have never used Google Earth Engine or Copernicus | [Path B — first time, from zero (≈20 min)](#path-b--first-time-from-zero-20-min) |
| Something failed | [Troubleshooting](#troubleshooting) |

Whichever path you take, read the
[shared-server rules](#shared-server-rules-cglabs--read-this-first) first
— they say where things go and what must never be shared.

What each credential unlocks:

| Provider | Needed for | What you place on the server |
| --- | --- | --- |
| Copernicus CDS | AgERA5 climate, SEAS5 forecasts | token in `~/.cdsapirc` |
| Google Earth Engine (GEE) | `sentinel/script1`, future MODIS | file `~/.config/earthengine/credentials` |
| CHIRPS, SoilGrids, DEM | nothing — no account needed | — |

---

## Shared-server rules (CGLabs) — read this first

CGLabs gives **each person their own private home** (`/home/jovyan` is
yours alone) plus one **shared folder** (`common_data`) that every AgWise
user can read and write. The team policy in one line: **data is shared,
credentials are personal.**

**Data → shared.** Everyone points `AGWISE_DATA_ROOT` at the shared cache
root; the first person to request a dataset pays the download and everyone
else gets a cache hit. Nothing secret is ever stored there.

**Credentials → personal, always.** Each person creates their **own** free
accounts and keeps the tokens in their **own** home:

- your own CDS account → your token in `~/.cdsapirc` (`chmod 600`)
- your own Google sign-in → your `~/.config/earthengine/credentials` (`chmod 600`)
- your own GitHub PAT (fine-grained, this repo only, with an expiry date)

Never place a credential in `common_data`, in the repo, in a notebook, or
lend one to a colleague "just to test" — with this guide they can create
their own in ~10 minutes. Why it matters: a personal free token that leaks
is rotated in 2 minutes and affects only you; a shared one takes the whole
team down. CDS download queues are also **per account** — sharing one
token means the whole team waits in a single queue.

**GEE as a team: one project, individual sign-ins.** The project owner
adds each member once: <https://console.cloud.google.com> → *IAM* →
*Grant access* → colleague's Gmail → role **Earth Engine Resource
Writer**. Then everyone uses the same `gee_project="ee-<team-project>"`
in scripts, but each authenticates with their own account and file.
(Members added this way can skip step B3 below — jump straight to B4.)

**Unattended/scheduled jobs only** (no human to sign in): use a GEE
[service account](https://developers.google.com/earth-engine/guides/service_account)
and a dedicated CDS "bot" account, keys stored only in the home of the
account running the job. Not needed for interactive work.

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

Both worked? Then tell `agwise-data` your project once (add to
`~/.bashrc`), so `get_ndvi`/`get-modis` find it:

```bash
export AGWISE_GEE_PROJECT=ee-<yourname>
```

You are done. Anything failed → [Troubleshooting](#troubleshooting).

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
So never run the auth command on CGLabs. Do it on your laptop, then copy
one small file (B5).

Two more traps we already hit, so you don't have to: GEE **no longer
works on Python 3.9** (the default on many Macs), and macOS Python often
lacks SSL certificates. The steps below avoid both — tested on a real
Mac. Do them once, in order, in the Terminal:

**Step 1.** Install a modern Python if you don't have one (Mac):

```bash
brew install python@3.11
```

**Step 2.** Create a clean environment with it:

```bash
$(brew --prefix)/opt/python@3.11/bin/python3.11 -m venv ~/ee-env
```

(Windows: install Python 3.11 from python.org, then
`py -3.11 -m venv %UserProfile%\ee-env`.)

**Step 3.** Activate it:

```bash
source ~/ee-env/bin/activate
```

The prompt must now start with `(ee-env)`. **If it doesn't, stop and
repeat this step** — nothing below works outside the environment.
(Windows: `%UserProfile%\ee-env\Scripts\activate`.)

**Step 4.** Install Earth Engine inside the environment:

```bash
pip install --upgrade earthengine-api certifi
```

**Step 5.** (Mac only, once) Pin the SSL certificates — prevents the
`CERTIFICATE_VERIFY_FAILED` error forever:

```bash
echo 'export SSL_CERT_FILE=$(python -m certifi 2>/dev/null)' >> ~/.zshrc
echo 'export REQUESTS_CA_BUNDLE=$(python -m certifi 2>/dev/null)' >> ~/.zshrc
```

Then close the Terminal, open a new one, and re-activate (Step 3).

**Step 6.** Authenticate — use this exact form (the bare `earthengine`
command can silently resolve to the old system Python):

```bash
python -m ee.cli.eecli authenticate
```

Your browser opens → sign in with the account from B3 → **Allow** → when
the browser says it succeeded, close that tab.

**Step 7.** Confirm the credentials file exists:

```bash
ls -la ~/.config/earthengine/
```

You must see a file named `credentials` (no extension). That file is your
key: **never share it or upload it to GitHub** — the only place it goes
is your own home on CGLabs (B5).

- Windows path: `C:\Users\<you>\.config\earthengine\credentials`

**Step 8.** Test — replace with your real project ID; must print `2`:

```bash
python -c "import ee; ee.Initialize(project='ee-<yourname>'); print(ee.Number(1).add(1).getInfo())"
```

Later sessions on the laptop only ever need Step 3 again — certificates
and credentials are already saved.

No Python on the laptop at all? Use Colab as the browser machine:
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
  your `earthengine-api` is old (retired OAuth flow). Fix: follow B4 **on
  your laptop** (fresh env, `pip install --upgrade earthengine-api`),
  authenticate there, copy the file (B5).
- `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` →
  you are running the old system Python (3.9). Make sure the prompt shows
  `(ee-env)` (B4 Step 3) and call the CLI as
  `python -m ee.cli.eecli authenticate`, **not** bare `earthengine`.
- `SSL: CERTIFICATE_VERIFY_FAILED` → missing certificates (common on
  Mac). Run B4 Step 5, close and reopen the Terminal, re-activate the
  env.
- Prompt doesn't show `(ee-env)`, or `command not found: python` → the
  environment is not active. Run B4 Step 3 again.
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
