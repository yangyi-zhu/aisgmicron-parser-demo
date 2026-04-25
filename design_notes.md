# Log Parser Demo: Detailed Design Notes

This document is a deeper companion to `README.md`. It explains how the project is structured, why each file exists, how data moves through the system, and what assumptions each parser makes.

For testing instructions for the demo, please read our [README.md](./README.md).

## 1. What this project is trying to do

At a high level, this project simulates a semiconductor hardware log ingestion workflow:

1. Generate realistic vendor-specific logs in multiple formats.
2. Detect new files arriving in a watched folder.
3. Convert those heterogeneous logs into one standard JSON schema.
4. Persist both raw and normalized data into SQLite.
5. Show the result in a lightweight FastAPI web UI.
6. Let a tester generate new files and selectively delete ingested items from the browser.

The core idea is that real factory or equipment logs are messy:

- Different vendors emit different file formats.
- The same metric can appear under different names.
- Some logs are very structured, while others are mostly human-readable text.

This repo demonstrates a practical normalization pattern:

- Use deterministic parsing for stable formats.
- Use AI only for the ambiguous or highly variable text formats.
- Store both the original and the standardized form.

## 2. Project layout and responsibility by file

### `combgen.py`

This is the synthetic log generator. It creates sample logs for multiple vendor styles so the ingestion pipeline can be exercised without requiring access to real fab equipment data.

Its main job is to simulate diversity:

- nested JSON
- large XML tag sets
- CSV time-series
- plain text warning/event logs
- binary payloads
- syslog-like lines
- key/value state dumps
- parquet sensor dumps

The generator intentionally varies values and occasionally injects anomalies so the downstream standardization logic can produce alarms and non-ideal health scores.

### `standardize.py`

This is the standalone standardization script. It is designed to process files directly and output a normalized `output.json`.

The current version follows a hybrid normalization approach:

1. Deterministic parse first
2. Confidence scoring
3. Gemini fallback only when confidence is too low or fields are missing
4. Learning reusable terms into `standardize_mappings.json`

This file is now the most advanced normalization implementation in the repo.

### `standardize_mappings.json`

This is the persistent rule cache and seed mapping file for `standardize.py`.

It currently contains:

- `equipment_hints`: terms that imply `ETCH`, `CVD`, `LITHO`, or `METROLOGY`
- `alarm_terms`: raw phrases mapped to normalized alarm codes
- `status_terms`: words that imply alarm-like severity
- `metric_aliases`: known alternate names for standard metrics
- `source_field_mappings`: vendor-specific source-to-standard metric hints

Right now `standardize.py` actively uses:

- `equipment_hints`
- `alarm_terms`
- `status_terms`

The `metric_aliases` and `source_field_mappings` sections are pre-seeded for future expansion and documentation value, but they are not yet used directly by the parsing logic.

### `pipeline.py`

This is the live ingestion engine used by the web app. It is separate from `standardize.py` and contains its own parser implementations plus database logic and the file watcher.

This separation matters:

- `standardize.py` is a standalone batch-style normalizer.
- `pipeline.py` is the always-on ingestion service for files dropped into `watched_logs/`.

Both normalize into the same conceptual schema, but they are not yet unified into one shared parsing library.

### `app.py`

This is the FastAPI entry point. It wires together the watcher, ingestion pipeline, static assets, templates, and HTTP routes.

### `demo_stream.py`

This is a convenience script that continuously emits generated logs into the watched directory so the UI can be demonstrated in real time.

### `templates/index.html`

This is the Jinja template for the web UI. It renders:

- a modal popup for generating new test files by vendor type
- the watch directory path
- a list of ingested logs
- checkbox controls for multi-select deletion
- a selected raw log
- a selected standardized view
- the standardized JSON blob

### `static/style.css`

This provides the layout and visual styling for the UI. It is intentionally simple and functional.

### `requirements.txt`

This lists the main Python dependencies required by the demo app, including the FastAPI form-handling dependency used by the generate and delete actions.

### `README.md`

This is the quick-start document. It focuses on how to run the system. This `docs.md` focuses on why the code is structured the way it is.

## 3. The standard schema used across the project

Nearly every file in the repo converges on the same normalized shape:

```json
{
  "base_info": {
    "timestamp_iso": null,
    "tool_id": null,
    "equipment_type": null,
    "recipe_name": null,
    "process_step": null
  },
  "normalized_metrics": {
    "temperature_c": null,
    "pressure_torr": null,
    "rf_forward_w": null,
    "rf_reflected_w": null,
    "gas_flow_sccm": null
  },
  "health_status": {
    "is_alarm": null,
    "alarm_code": null,
    "health_score": null
  }
}
```

The schema is intentionally small. It picks a handful of cross-vendor fields that are easy to compare in a UI or database:

- base identity and timing information
- a few representative process metrics
- a compact health/alarm summary

This is a common first step in ingestion systems: define a minimal universal schema before attempting a broader semantic model.

## 4. How `combgen.py` works

`combgen.py` is centered on the `AdvancedSemiLogGenerator` class.

### Shared generator design

The constructor creates reusable pools of synthetic IDs and names:

- lot IDs
- recipes
- operators
- chambers
- tools

This makes the generated logs look varied without requiring large static fixtures.

### Vendor A: nested JSON

`generate_vendor_a_complex()` produces a highly structured etch-style record with:

- event header metadata
- lot context
- RF metrics
- gas flows
- temperatures
- vacuum values
- active alarms

This is the easiest class of input to normalize because field names are explicit and hierarchy is stable.

### Vendor B: XML

`generate_vendor_b_xml()` creates a flat set of XML tags for a CVD-like tool.

It includes:

- heater zone temperatures
- chamber and foreline pressure
- gas setpoints
- high and low frequency RF values
- alarm ID/text

The anomaly pattern is mainly a heater-zone deviation and a non-zero alarm ID.

### Vendor C: CSV time-series

`generate_vendor_c_timeseries()` creates many rows representing a lithography process stream.

Important design details:

- `current_step` increments every 20 rows
- step 3 introduces drift
- later rows therefore encode process progression and slight degradation

The downstream parser usually uses the latest row as the current state snapshot.

### Vendor D: plain text log

`generate_vendor_d_text()` simulates multi-line event and warning blocks. This format is intentionally noisy and more human-oriented than machine-oriented.

It includes:

- machine identifier
- release/module/component context
- warning text
- scan-time details
- a bracketed symbolic warning ID

This is the kind of format that often pressures teams toward AI fallback because the structure is less stable than JSON or XML.

### Vendor E: binary

`generate_vendor_e_binary()` packs numeric values with `struct.pack`.

The fields represent process step, recipe hash, status bits, powers, voltages, flow values, pump temperature, and cycle count. Binary formats are compact and efficient but require a fixed binary contract between producer and parser.

### Vendor F: syslog

`generate_syslog()` emits RFC3164-style-ish lines with:

- priority
- timestamp
- host
- app tag
- severity
- message

This is semi-structured text: the envelope is predictable, but the message body may vary.

### Vendor G: key/value dump

`generate_key_value()` emits machine state as `key=value` pairs. This format is easy to parse but often suffers from inconsistent naming across vendors.

### Vendor H: parquet

`generate_vendor_h_parquet()` creates a columnar sensor dataset using pandas.

Important logic:

- values are produced as aligned columns
- a burst of anomalies is injected in the middle of the dataset
- consumers typically use the latest row or a tail preview

This mimics high-volume telemetry rather than single-event logs.

### Export helpers

The export methods in `combgen.py` simply serialize the generated content to disk in each file format. They keep generation logic separate from file-writing logic, which makes the class easier to reuse.

## 5. How `demo_stream.py` works

`demo_stream.py` is a small orchestration layer on top of `combgen.py`.

### `emit_one(...)`

This function chooses one synthetic vendor format at random and writes a single file into the target output directory.

Why this matters:

- it simulates a mixed stream of incoming files
- it produces varied suffixes that exercise all parser branches
- it lets the UI populate gradually instead of all at once

### `main()`

The script exposes:

- `--out-dir`
- `--count`
- `--interval`

It then loops, emitting one file per iteration and sleeping between writes.

This behavior is useful for demos because the watcher can discover files incrementally, making the UI feel live.

## 6. How the live ingestion path works in `pipeline.py`

`pipeline.py` is the operational center of the demo app.

## 6.1 Main components

There are four important pieces:

1. `AIStandardizer`
2. `LogRepository`
3. `LogIngestionPipeline`
4. `FilePollWatcher`

### `AIStandardizer`

This class encapsulates the Gemini API call used for text-heavy logs.

Its logic is:

1. Read Gemini configuration from environment variables
2. If an API key exists, call the Gemini endpoint
3. Request strict JSON output using a response schema
4. If the API call fails, fall back to local regex/rule parsing

This is a good resilience pattern because the system still works when the API is unavailable.

Important environment variables:

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_BASE_URL`

### `LogRepository`

This class owns SQLite persistence.

It initializes a table called `ingested_logs` with:

- source file metadata
- raw log text
- full standardized JSON
- flattened fields for easier filtering/display
- `created_at`

Important design choice:

There is a uniqueness constraint on `(source_path, source_mtime)`. That means if the same file path changes on disk, the pipeline treats it as a new version. If the file has not changed, it is skipped.

The repository also supports targeted row lookup and deletion so the UI can remove selected entries instead of clearing the whole database.

### `LogIngestionPipeline`

This class ties together:

- watch directory traversal
- per-file parsing
- AI standardization
- database insertion

`scan_once()` is the core function:

1. Iterate over files in the watched folder
2. Skip unsupported suffixes
3. Skip already-ingested file versions
4. Parse and standardize the file
5. Insert the result into SQLite

The pipeline also owns two UI-facing helper behaviors:

- `generate_requested_files(...)`: generate one or more new files by vendor code and ingest them immediately
- `delete_logs(...)`: remove selected rows and delete their source files from disk

### `FilePollWatcher`

This class runs `scan_once()` in a background thread on a fixed interval. It is a simple polling watcher rather than an OS-native filesystem event watcher.

Why polling is acceptable here:

- simpler demo code
- fewer platform-specific edge cases
- enough for small local directories

For a production system, native file notifications or a queue-backed ingestion trigger would usually be preferred.

## 6.2 How each file type is handled in `pipeline.py`

### `.json`

Handled by `parse_vendor_a_json`.

The parser reads nested fields directly and maps:

- `ESC_Temp` -> `temperature_c`
- `ChamberPressure_mTorr / 1000` -> `pressure_torr`
- `SourcePower_W` -> `rf_forward_w`
- `SourceReflected_W` -> `rf_reflected_w`
- `CF4` flow -> `gas_flow_sccm`

### `.xml`

Handled by `parse_vendor_b_xml`.

The parser:

- converts child tags into a simple dict
- averages heater zone temperatures
- sums selected gas fields
- interprets alarm IDs and text

This is a common pattern when a structured input exposes many similar per-zone or per-channel metrics.

### `.csv`

Handled by `parse_vendor_c_csv`.

The parser reads all rows and uses the last row as the current snapshot. This is a simplification, but it is often reasonable for time-series logs when the UI wants the latest equipment state.

### `.log`

Handled by `AIStandardizer.standardize_text`.

This means `.log` files go through:

- Gemini first if enabled
- local fallback parser otherwise

That path currently covers both vendor D and vendor F because both use `.log` suffixes.

### `.txt`

Also handled by `AIStandardizer.standardize_text`.

This covers vendor G state dumps and any other plain-text inputs.

### `.bin`

Handled by `parse_vendor_e_binary`.

The raw bytes are also converted into a human-readable hex string for storage and UI display.

### `.parquet`

Handled by `parse_vendor_h_parquet`.

The parser reads the parquet file into a DataFrame, takes the last row as the normalized state, and stores a small tail preview as the raw display text.

## 6.3 Helper functions in `pipeline.py`

These functions keep parser code cleaner and more defensive:

- `normalize_schema`: ensures the result always matches the universal structure
- `flatten_standardized`: extracts nested fields into a flat dict for SQLite columns
- `gemini_response_schema`: tells Gemini the exact JSON shape expected
- `extract_gemini_text`: pulls the JSON text from Gemini’s response payload
- `parse_dt`: normalizes multiple timestamp string formats into ISO
- `infer_equipment_type`: infers `ETCH`, `CVD`, `LITHO`, or `METROLOGY` from tool IDs
- `safe_divide`, `to_int`, `to_float`, `average`, `sum_ignore_none`, `round_or_none`: utility conversion helpers

This style is important in ingestion code because malformed or incomplete fields are common.

## 7. How `app.py` exposes the pipeline

`app.py` is intentionally thin. Its purpose is to bind the ingestion engine to HTTP routes.

### Startup flow

On startup:

1. Ensure the watch directory exists
2. Run one immediate scan so existing files appear
3. Start the background polling watcher

### Shutdown flow

On shutdown:

1. Stop the watcher thread

### Routes

#### `GET /`

Renders the main HTML page.

It loads:

- recent logs from the database
- one selected log, defaulting to the latest
- parsed standardized JSON for pretty display in the template

#### `GET /api/logs`

Returns recent database rows as JSON. This is useful for lightweight inspection or future UI extensions.

#### `POST /scan`

Triggers an on-demand scan of the watch directory, then redirects back to `/`.

#### `POST /generate`

Accepts one count per vendor type, generates new files in `watched_logs/`, runs a scan immediately, and redirects back to `/`.

#### `POST /delete`

Deletes the selected log rows and their underlying source files, then redirects back to `/`.

Deleting the source files matters because otherwise the polling watcher would ingest them again on the next scan.

## 8. How the UI works

### `templates/index.html`

The template uses a simple two-column layout with a sidebar.

Important behaviors:

- auto-refresh every 5 seconds through JavaScript polling
- auto-refresh pauses while the generate modal is open or while the user has pending form selections
- numeric inputs for `JSON (A)` through `PARQUET (H)` generation
- a select-all checkbox plus per-row checkboxes for deletion
- log selection via `/?log_id=...`
- a raw-content panel and a cleaned-output panel shown side by side

The cleaned panel mixes:

- flat database fields for fast display
- the full `standardized_json` blob for completeness

This is useful because users can quickly inspect key fields without losing access to the exact normalized structure.

### `static/style.css`

The CSS uses a straightforward admin-style layout:

- top header bar
- left sidebar list
- modal generator controls that do not consume sidebar space
- right content area
- responsive collapse on smaller screens

The design is deliberately conservative because the goal is observability, not branding.

## 9. How `standardize.py` differs from `pipeline.py`

This is the most important architectural nuance in the repo.

Both files normalize logs, but they do it differently.

### `pipeline.py`

- used by the web app
- optimized for live watched-folder ingestion
- contains its own parsers
- uses Gemini for text logs directly when enabled

### `standardize.py`

- standalone batch-like standardization script
- now includes deterministic-first parsing
- scores translation confidence
- only calls Gemini as a fallback
- persists newly discovered mappings

### Practical implication

If you run the web app, you are currently using `pipeline.py` logic, not the newer `standardize.py` logic.

So the repo currently has two normalization implementations:

1. a demo-ingestion implementation in `pipeline.py`
2. a more advanced mapping-aware implementation in `standardize.py`

This is not inherently wrong, but it is worth knowing because future refactoring would likely move parsing into a shared module so both code paths behave consistently.

## 10. How the new mapping system in `standardize.py` works

`standardize.py` now uses a persistent mapping strategy.

### Step 1: load rule state

It loads `standardize_mappings.json` if present, then merges it on top of built-in defaults.

This means:

- the script always has some baseline heuristics
- learned rules survive across runs

### Step 2: deterministic parsing

It first tries vendor-specific deterministic parsing based on known patterns and file types.

This is faster, cheaper, and more reproducible than AI calls.

### Step 3: confidence scoring

The script computes a score based on:

- parser baseline confidence
- completeness of `base_info`
- completeness of `normalized_metrics`
- completeness of `health_status`

If the score is high enough, the deterministic result is accepted.

### Step 4: Gemini fallback

If confidence is too low, the script sends:

- the raw log
- the deterministic draft
- the current rules

to Gemini and asks for:

- a standardized output object
- reusable learned mappings

This is a controlled fallback rather than a free-form first pass.

### Step 5: rule learning

If Gemini returns new reusable mappings, they are merged into `standardize_mappings.json`.

This is the long-term efficiency mechanism:

- early runs may need more AI assistance
- later runs should become more deterministic as the rule base grows

## 11. Why there are some heuristic shortcuts

This project is a demo, so some normalization choices are intentionally simplified.

Examples:

- Some parsers use the last row of a time-series as the representative state.
- Some fields are inferred from tool names rather than explicitly provided by the source.
- Some metric conversions are semantic approximations rather than physically rigorous conversions.
- Health scores are heuristic values, not outputs from a learned reliability model.

These choices are appropriate for illustrating the architecture. In production, they would usually be replaced by:

- equipment-specific contracts
- unit-aware conversions
- stronger timestamp provenance
- better anomaly rules
- parser test fixtures

## 12. Notable inconsistencies and caveats

These are worth calling out explicitly.

### `requirements.txt` must cover both UI and normalization paths

The current project needs:

- `requests` for the Gemini HTTP call in `pipeline.py`
- `python-multipart` for FastAPI `Form(...)` handling
- `google-genai` and `xmltodict` for `standardize.py`

### Two parser stacks exist

As noted above, `pipeline.py` and `standardize.py` do not yet share one canonical parser implementation.

### Some text parser assumptions differ across files

`pipeline.py` contains one set of regex assumptions for text logs, while `standardize.py` now contains a different and more recent set. If consistency matters, unifying them would be a useful next step.

## 13. End-to-end data flow summary

There are effectively two main workflows in this repo.

### Workflow A: web demo

1. `demo_stream.py`, the web generator form, or manual file drops place logs into `watched_logs/`
2. `FilePollWatcher` notices them
3. `LogIngestionPipeline.scan_once()` processes them
4. `LogRepository` stores raw and normalized data in SQLite
5. `app.py` serves the stored data to the UI
6. The user can select rows in the sidebar and delete only the items they want removed

### Workflow B: standalone normalization

1. Files exist in the working directory
2. `standardize.py` scans supported patterns
3. Deterministic parsing runs first
4. Confidence is evaluated
5. Gemini is used only if confidence is too low
6. New reusable terms are written to `standardize_mappings.json`
7. Final results are written to `output.json`

## 14. Suggested next refactor if this project grows

If this demo becomes a longer-lived codebase, the most valuable structural improvement would be:

Create one shared normalization module and have both `pipeline.py` and `standardize.py` call into it.

That would give you:

- one source of truth for parser behavior
- one mapping engine
- one confidence model
- one AI fallback strategy
- fewer drift bugs between demo mode and batch mode

The second best improvement would be to add parser tests using saved fixtures from `combgen.py`.

## 15. Quick mental model

If you want one compact way to think about the repo:

- `combgen.py` creates the mess
- `demo_stream.py` turns it into a stream
- `pipeline.py` watches and ingests the stream
- `app.py` shows the results
- `standardize.py` is the more advanced offline normalizer
- `standardize_mappings.json` is the beginning of the reusable knowledge layer

That is the full logic of the system in one sentence: generate heterogeneous logs, normalize them into one schema, persist them, display them, and gradually reduce ambiguity with reusable mappings.
