# RC Continuous Authentication PoC

This prototype demonstrates continuous keystroke authentication using Reservoir Computing.

## What it does

- Generates synthetic keystroke streams for:
  - genuine user
  - impostor user
- Extracts windowed keystroke features
- Trains a sparse reservoir + ridge readout model
- Calibrates a decision threshold
- Evaluates FAR/FRR on held-out sequences
- Runs a takeover demo (genuine -> impostor) and reports detection timing

## Setup

```bash
cd /home/blackleg/ws/mitou_target/prototype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd /home/blackleg/ws/mitou_target/prototype
python3 scripts/run_poc.py
```

Optional parameters:

```bash
python3 scripts/run_poc.py --seed 7 --events-per-seq 360 --num-train 30 --num-calib 10 --num-test 10
```

## Notes

- This is a concept verification prototype, not production code.
- Input data is synthetic for fast iteration.
- Next step is replacing synthetic data with real keystroke capture logs.

## Frontend PoC (TypeScript)

A TypeScript frontend demo is available at:

- `prototype/frontend/index.html`

Quick run:

```bash
cd /home/blackleg/ws/mitou_target/prototype/frontend
python3 -m http.server 8080
```

## Frontend + RC inference API demo

To run the integrated server:

```bash
cd /home/blackleg/ws/mitou_target/prototype
python3 scripts/run_demo_api.py --port 8080
```

Then open `http://127.0.0.1:8080`.

## Real user enrollment -> authentication flow

1. `Enrollment (User Registration)`:
   - Type in the enrollment box.
   - Click `Add Sample` after each typing round.
   - Collect at least 4 samples (each sample should include at least about 24 keystrokes).
2. Click `Train RC Model`:
   - This runs RC training on your captured samples.
3. `Authentication Test`:
   - Type in the auth box and click `Run Authentication`.
   - The frontend replays window scores and shows risk/decision.

## API endpoints

- `GET /api/health`
- `GET /api/enroll/status`
- `POST /api/enroll`
- `POST /api/enroll/reset`
- `POST /api/authenticate`

Compatibility synthetic endpoint:

- `GET /api/session?mode=normal`
- `GET /api/session?mode=takeover`
