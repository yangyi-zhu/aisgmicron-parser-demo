import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline import FilePollWatcher, LogIngestionPipeline, normalize_schema

BASE_DIR = Path(__file__).resolve().parent
WATCH_DIR = Path(os.getenv("WATCH_DIR", BASE_DIR / "watched_logs"))
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "logs.db"))

app = FastAPI(title="Log Ingestion Demo")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

pipeline = LogIngestionPipeline(str(WATCH_DIR), DB_PATH)
watcher = FilePollWatcher(pipeline)

VENDOR_OPTIONS = [
    ("A", "JSON (A)"),
    ("B", "XML (B)"),
    ("C", "CSV (C)"),
    ("D", "LOG (D)"),
    ("E", "BIN (E)"),
    ("F", "SYSLOG (F)"),
    ("G", "TXT (G)"),
    ("H", "PARQUET (H)"),
]


@app.on_event("startup")
def on_startup() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.scan_once()
    watcher.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    watcher.stop()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, log_id: Optional[int] = None):
    logs = pipeline.repo.list_logs(limit=1000)
    selected = pipeline.repo.get_log(log_id) if log_id else pipeline.repo.get_log()
    selected_json = None
    if selected:
        selected_json = normalize_schema(json.loads(selected["standardized_json"]))
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "logs": logs,
            "selected": selected,
            "selected_json": selected_json,
            "vendor_options": VENDOR_OPTIONS,
            "watch_dir": str(WATCH_DIR),
        },
    )


@app.get("/api/logs")
def api_logs():
    rows = pipeline.repo.list_logs(limit=1000)
    return [dict(row) for row in rows]


@app.post("/scan")
def scan_now():
    pipeline.scan_once()
    return RedirectResponse(url="/", status_code=303)

@app.post("/generate")
def generate_logs(
    count_a: int = Form(0),
    count_b: int = Form(0),
    count_c: int = Form(0),
    count_d: int = Form(0),
    count_e: int = Form(0),
    count_f: int = Form(0),
    count_g: int = Form(0),
    count_h: int = Form(0),
):
    counts = {
        "A": max(0, count_a),
        "B": max(0, count_b),
        "C": max(0, count_c),
        "D": max(0, count_d),
        "E": max(0, count_e),
        "F": max(0, count_f),
        "G": max(0, count_g),
        "H": max(0, count_h),
    }
    pipeline.generate_requested_files(counts)
    return RedirectResponse(url="/", status_code=303)


@app.post("/delete")
def delete_logs(log_ids: list[int] = Form(default=[])):
    pipeline.delete_logs(log_ids)
    return RedirectResponse(url="/", status_code=303)
