# Log Parser Demo: Testing Guide

This file only contains testing instructions for running and exercising the demo.

For architecture, parser behavior, schema details, and design notes, read [docs.md](./docs.md).

## 1. Install Dependencies

From the project directory:

```bash
pip install -r requirements.txt
```

## 2. Optional Gemini Configuration

Text-heavy logs can use Gemini when an API key is present. If no key is configured, the app falls back to local parsing logic.

### PowerShell

```powershell
$env:GEMINI_API_KEY="your-key"
```

### Bash

```bash
export GEMINI_API_KEY=your-key
```

Optional base URL override:

### PowerShell

```powershell
$env:GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta"
```

### Bash

```bash
export GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

## 3. Start the App

From the project directory:

```bash
py -m uvicorn app:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## 4. Generate Test Data

You have two testing options.

### Option A: Use the in-app generator

1. Open `Generate Test Files`
2. Enter counts for any vendor types you want to generate
3. Submit the form
4. Wait for the new rows to appear in the sidebar

This is the easiest way to exercise the UI and parser flow.

### Option B: Use the CLI generator

In a second terminal:

```bash
py demo_stream.py --count 12 --interval 1
```

This continuously writes mixed test files into `watched_logs/`.

## 5. Test Checklist

Use this checklist to verify the current behavior.

1. Start the app and confirm the page loads without a `500`.
2. Generate one or more files for several vendor types.
3. Confirm new rows appear in the `Ingested Logs` sidebar.
4. Click a row and confirm `Raw Log` and `Standardized Output` both render.
5. Confirm the `Copy JSON` button copies the standardized JSON.
6. Confirm section cards only appear when they contain data.
7. Confirm `Expanded Metrics` only shows extra populated metrics, not the core summary fields again.
8. Select several rows, use `Select All` if needed, and confirm `Delete Selected` removes those rows and their source files.

## 6. Testing Vendor Coverage

Suggested vendor checks:

- `JSON (A)`: confirm lot, wafer, gas, RF, and temperature fields populate.
- `XML (B)`: confirm heater-zone and pressure-related fields populate.
- `CSV (C)`: confirm lithography alignment, dose, and stage-position fields populate.
- `LOG (D)`: confirm warning/event blocks parse and scan timing fields populate when present.
- `BIN (E)`: confirm binary-derived RF, flow, and status-derived fields populate.
- `SYSLOG (F)`: confirm event context always populates, and fresh generated rows may also populate inline syslog metrics when present.
- `TXT (G)`: confirm state-dump values like uptime, flow, and chamber temperature populate.
- `PARQUET (H)`: confirm sensor-derived helium pressure, backside temperature, and flow populate.

## 7. Resetting Test State

If you want a clean run:

1. Stop the app
2. Delete rows from the UI or remove `logs.db`
3. Clear files from `watched_logs/`
4. Restart the app
5. Generate fresh files again

This is especially useful after parser or schema changes, since older rows in `logs.db` keep the older normalized output they were ingested with.

## 8. Files to Watch During Testing

- `watched_logs/`: incoming generated files
- `logs.db`: persisted ingested records
- `output.json`: output from standalone `standardize.py` runs

## 9. Standalone Standardizer Check

If you want to test the standalone normalizer separately from the web app:

```bash
py standardize.py
```

This writes results to `output.json` and uses `standardize_mappings.json` for rule seeding and learned mappings.
