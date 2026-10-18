# Jagajanin: offline pregnancy-risk triage for village midwives

hiff.ai's entry for the **Google Cloud × Hack2Skill AI Builder Cup 2026** (JAPAC). Jagajanin was first built as a prototype for Hack-Nation × World Bank "Small AI for Development" (October 2026). This repo is that prototype, hardened to run as a public demo on Google Cloud Run.

A midwife at a Puskesmas (Indonesian primary health centre) connects her phone to the clinic's ultrasound scanner and follows six guided probe sweeps. She needs no ultrasound training. The phone works out whether the mother needs a specialist, offline. When a signal returns, the case goes to a tele-obstetrician, who validates it and sends the referral plan back to the midwife.

**Live demo:** _the Cloud Run link goes here once it is deployed._ Open the midwife app with `/?lang=en` and the console with `/doctor.html?lang=en`.

The demo uses a fictional caseload. Please don't enter real patient information.

## Try it in five minutes

1. Open the **midwife app** (`/?lang=en`). Tap **New Visit**, pick a demo scenario, fill in the mother's details and tick any danger signs.
2. Follow the **six sweeps**: an arrow on a belly map shows each path, and a voice reads the instruction (switch to JV for Javanese).
3. The phone analyses the sweeps and shows one of four answers, with each reason and its confidence. Agree, or change the decision with a reason, then **save**.
4. Open the **specialist console** from the link in the demo banner. Your case arrives at the top of a queue from five Puskesmas, sorted by risk and then waiting time, marked **NEW**.
5. Watch the six sweeps, confirm the referral and pick a hospital: the console ranks the nearest ones able to treat her first and marks those that can't. Choose the first steps and send.
6. Back on the phone, the plan arrives. Tick the steps and confirm. The specialist sees the confirmation, and the loop is closed.

To see the fail-safe, pick the poor-sweep scenario: the AI isn't sure, so it answers **TIDAK YAKIN** (not sure) and asks for a rescan instead of saying "safe".

## The two parts

| | Who uses it | What it does |
|---|---|---|
| **Midwife app** (`index.html`, `app.js`) | The midwife, on her own Android phone | Intake and Buku KIA danger signs → 6-sweep protocol with voice guidance → on-phone result → her decision → saved offline, synced when a signal appears |
| **Tele-specialist console** (`doctor.html`, `doctor.js`) | An obstetrician (Sp.OG) covering many Puskesmas | Queue sorted by risk and waiting time → six sweep videos and the AI findings → validate → nearest facility able to treat the mother → first steps sent back to the midwife |

The result is always one of four fixed outcomes: **AMAN** (safe), **HATI-HATI** (caution), **RUJUK** (refer) or **TIDAK YAKIN** (not sure).

## Guardrails

- **Fixed outputs only.** The tool chooses from four outcomes and a fixed list of reasons. It never generates free text.
- **It says when it isn't sure.** Any scan parameter below an 80% confidence floor counts as "not sure", and a "not sure" can never come out as AMAN. A confident RUJUK finding always wins. See `rules.js`.
- **The midwife decides.** She must agree or override before saving. An override needs a written reason, and both the AI's output and her decision are logged.
- **A doctor validates before anything goes back.** The pre-referral steps in `protocols.js` reach the midwife only after the tele-specialist picks and confirms them.
- **Clinical signs still count.** Danger signs, blood pressure and the mother's age are combined with the scan findings, so the tool still helps when no scanner is available.

## Why six sweeps

The six sweeps follow the published **Obstetric Sweep Protocol** from Imaging the World: three sweeps from the pubic bone up to the breastbone and three side to side, designed so that an untrained person can learn them in a few hours and capture a scan that shows fetal number, presentation and placenta position.

- DeStigter K.K. et al. *Low-cost teleradiology for rural ultrasound.* IEEE Global Humanitarian Technology Conference, 2011, pp. 290–295. doi:[10.1109/GHTC.2011.39](https://doi.org/10.1109/GHTC.2011.39)
- van den Heuvel T.L.A. et al. *Automated fetal head detection and circumference estimation from free-hand ultrasound sweeps using deep learning in resource-limited countries.* Ultrasound in Medicine & Biology 45(3):773–785, 2019. doi:[10.1016/j.ultrasmedbio.2018.09.015](https://doi.org/10.1016/j.ultrasmedbio.2018.09.015)

## Status

| | Status |
|---|---|
| Offline PWA, 6-sweep protocol, fusion and fail-safe rules, midwife decision log | Built (prototype) |
| Store-and-forward sync and tele-specialist console with referral routing | Built (prototype) |
| Bahasa Indonesia, Basa Jawa and English interface; sweep guidance in Bahasa and Javanese | Built (Javanese text pending native-speaker review) |
| Public demo on Cloud Run: free tier, one instance, hardened server | Built for the AI Builder Cup |
| On-device model in this prototype | **Scripted demo outputs**, labelled in the app. The trained Jagajanin model isn't in this repo; `model.js` defines the output contract it plugs into |
| Clinical thresholds and pre-referral action lists | Draft, pending clinical sign-off |

## Data in this repo

There's no patient data here.
- `data/demo-sweeps/` holds **synthetic** sweep clips generated by `make_demo_sweeps.py`. Every frame is stamped "SIMULASI · bukan data pasien" (simulation, not patient data).
- The scanner stream in the midwife app is simulated speckle, labelled "simulasi" on screen.
- `seed_demo.py` creates a **fictional** caseload: made-up mothers, midwives, Puskesmas and facilities. The doctor shown in the console is a demo persona.

## Run it locally

You need Python 3.10 or later. It uses the standard library only.

```bash
python3 server.py --seed    # midwife app: http://localhost:8765/?lang=en
                            # console:     http://localhost:8765/doctor.html?lang=en
python3 tests/test_server.py
```

Leave out `--seed` for an empty console. Add `?clean=1` to swap the "DEMO MODE" banner for a small caption (for screen recordings). To test on a phone on the same Wi-Fi, open `http://<your-computer's-LAN-IP>:8765`.

## Deploy to Google Cloud Run

```bash
gcloud auth login
PROJECT=<gcp-project-id> ./deploy-cloudrun.sh    # prints the midwife and console URLs
```

The demo keeps its cases inside the container, so the service is set up to stay consistent and free:

- **At most one instance** (`--max-instances 1`), so the phone and the console always see the same cases.
- **Scales to zero** with request-based billing, so it runs inside Cloud Run's free tier. The first visit after a quiet spell waits a few seconds for a cold start, and a fresh instance starts with a freshly seeded caseload. The phone re-sends any assessment the server no longer has.
- **Stays tidy on a public link.** Only the app's own files are served. Uploads are capped (30 MB per sweep, 300 MB in total, oldest evicted first, at most 300 visitor cases). On a long-running instance the demo caseload is re-seeded with fresh timestamps every 6 h once nobody has used it for 30 min. `GET /healthz` is there for uptime checks.

## Files

| File | Role |
|---|---|
| `index.html`, `app.js` | Midwife app (offline PWA) |
| `doctor.html`, `doctor.js` | Tele-specialist console |
| `rules.js` | Fusion and fail-safe layer: confidence floor, danger signs, blood pressure, age, dating, presentation, fetal weight |
| `model.js` | Model adapter: the output contract and the scripted demo scenarios |
| `protocols.js` | Urgency tiers, demo facilities, draft pre-referral actions |
| `i18n.js` | Bahasa Indonesia, Basa Jawa and English strings |
| `sw.js`, `manifest.json` | Offline caching and PWA install |
| `server.py` | Small telemedicine API and static server, standing in for a production backend |
| `seed_demo.py` | Fictional caseload for the console |
| `make_demo_sweeps.py` | Generates the synthetic sweep clips (needs numpy, Pillow and ffmpeg) |
| `Dockerfile`, `deploy-cloudrun.sh` | Container image and the Cloud Run deployment |
| `tests/test_server.py` | Server checks: allowlist, input validation, upload caps, eviction, re-seed |

`server.py` is a demo server. It has no authentication and keeps data unencrypted inside the container, so don't use it with real patient data.

---

© 2026 hiff.ai. All rights reserved. Shared publicly for AI Builder Cup judging.
