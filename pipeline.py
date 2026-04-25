import binascii
import csv
import json
import os
import re
import sqlite3
import struct
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.dom import minidom

import pandas as pd
import requests

from combgen import AdvancedSemiLogGenerator

UNIVERSAL_SCHEMA_EXAMPLE = {
    "base_info": {
        "timestamp_iso": None,
        "tool_id": None,
        "equipment_type": None,
        "recipe_name": None,
        "process_step": None,
    },
    "process_context": {
        "lot_id": None,
        "wafer_id": None,
        "slot_id": None,
        "step_time_sec": None,
        "uptime_seconds": None,
    },
    "event_context": {
        "source_vendor": None,
        "source_format": None,
        "event_id": None,
        "severity": None,
        "event_class": None,
        "raw_message": None,
        "status": None,
        "interlock_status": None,
    },
    "normalized_metrics": {
        "temperature_c": None,
        "pressure_torr": None,
        "rf_forward_w": None,
        "rf_reflected_w": None,
        "gas_flow_sccm": None,
        "bias_power_w": None,
        "bias_reflected_w": None,
        "foreline_pressure_torr": None,
        "helium_pressure_psi": None,
        "laser_power_mw": None,
        "lens_temp_c": None,
        "pump_temp_c": None,
        "wall_temp_c": None,
        "showerhead_temp_c": None,
        "heater_zone_avg_c": None,
        "dose_mj_cm2": None,
        "focus_offset_nm": None,
        "alignment_error_x_nm": None,
        "alignment_error_y_nm": None,
        "leveling_z_um": None,
        "stage_x_pos_mm": None,
        "stage_y_pos_mm": None,
        "scan_time_ms": None,
        "required_scan_time_ms": None,
        "throttle_valve_angle_deg": None,
        "turbo_pump_speed_rpm": None,
        "spindle_speed_rpm": None,
    },
    "health_status": {
        "is_alarm": None,
        "alarm_code": None,
        "health_score": None,
    },
    "vendor_metrics": {},
}

UNIVERSAL_PROMPT = """
You are a semiconductor data engineer. Convert the provided raw machine log into a JSON object.
STRICTLY follow this schema:
{
  "base_info": {
    "timestamp_iso": "ISO8601 string or null",
    "tool_id": "string or null",
    "equipment_type": "ETCH | CVD | LITHO | METROLOGY | null",
    "recipe_name": "string or null",
    "process_step": "int or null"
  },
  "process_context": {
    "lot_id": "string or null",
    "wafer_id": "string or null",
    "slot_id": "int or null",
    "step_time_sec": "number or null",
    "uptime_seconds": "int or null"
  },
  "event_context": {
    "source_vendor": "string or null",
    "source_format": "string or null",
    "event_id": "string or null",
    "severity": "string or null",
    "event_class": "string or null",
    "raw_message": "string or null",
    "status": "string or null",
    "interlock_status": "string or null"
  },
  "normalized_metrics": {
    "temperature_c": "float or null",
    "pressure_torr": "float or null",
    "rf_forward_w": "int or null",
    "rf_reflected_w": "int or null",
    "gas_flow_sccm": "int or null",
    "bias_power_w": "int or null",
    "bias_reflected_w": "int or null",
    "foreline_pressure_torr": "float or null",
    "helium_pressure_psi": "float or null",
    "laser_power_mw": "float or null",
    "lens_temp_c": "float or null",
    "pump_temp_c": "float or null",
    "wall_temp_c": "float or null",
    "showerhead_temp_c": "float or null",
    "heater_zone_avg_c": "float or null",
    "dose_mj_cm2": "float or null",
    "focus_offset_nm": "int or null",
    "alignment_error_x_nm": "int or null",
    "alignment_error_y_nm": "int or null",
    "leveling_z_um": "float or null",
    "stage_x_pos_mm": "float or null",
    "stage_y_pos_mm": "float or null",
    "scan_time_ms": "float or null",
    "required_scan_time_ms": "float or null",
    "throttle_valve_angle_deg": "float or null",
    "turbo_pump_speed_rpm": "int or null",
    "spindle_speed_rpm": "int or null"
  },
  "health_status": {
    "is_alarm": "bool or null",
    "alarm_code": "string or null",
    "health_score": "int or null"
  },
  "vendor_metrics": {}
}
If data is missing, use null. Preserve useful vendor-specific detail in vendor_metrics. Return JSON only.
""".strip()

SUPPORTED_SUFFIXES = {".json", ".xml", ".csv", ".log", ".txt", ".bin", ".parquet"}


class AIStandardizer:
    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        self.enabled = bool(self.api_key)

    def standardize_text(self, raw_text: str) -> Dict[str, Any]:
        if self.enabled:
            try:
                response = requests.post(
                    f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent",
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {
                                        "text": f"{UNIVERSAL_PROMPT}\n\nRaw Log:\n{raw_text}",
                                    }
                                ],
                            }
                        ],
                        "generationConfig": {
                            "temperature": 0,
                            "responseMimeType": "application/json",
                            "responseJsonSchema": gemini_response_schema(),
                        },
                    },
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                content = extract_gemini_text(payload)
                return normalize_schema(json.loads(content))
            except Exception:
                pass
        return normalize_schema(self._fallback_parse(raw_text))

    def _fallback_parse(self, raw_text: str) -> Dict[str, Any]:
        if "Machine:" in raw_text and ("SYSTEM WARNING" in raw_text or "SYSTEM EVENT" in raw_text):
            return parse_vendor_d_text(raw_text)
        if "SEMI_APP:" in raw_text:
            return parse_vendor_f_syslog(raw_text)
        if "system_id=" in raw_text or "status=" in raw_text:
            return parse_vendor_g_key_value(raw_text)
        return UNIVERSAL_SCHEMA_EXAMPLE


class LogRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingested_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_mtime REAL NOT NULL,
                raw_content TEXT NOT NULL,
                standardized_json TEXT NOT NULL,
                timestamp_iso TEXT,
                tool_id TEXT,
                equipment_type TEXT,
                recipe_name TEXT,
                process_step INTEGER,
                temperature_c REAL,
                pressure_torr REAL,
                rf_forward_w INTEGER,
                rf_reflected_w INTEGER,
                gas_flow_sccm INTEGER,
                is_alarm INTEGER,
                alarm_code TEXT,
                health_score INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_path, source_mtime)
            )
            """
        )
        conn.commit()
        conn.close()

    def has_file_version(self, source_path: str, source_mtime: float) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM ingested_logs WHERE source_path = ? AND source_mtime = ? LIMIT 1",
            (source_path, source_mtime),
        ).fetchone()
        conn.close()
        return row is not None

    def insert_log(self, source_path: str, source_mtime: float, raw_content: str, standardized: Dict[str, Any]) -> int:
        standardized = normalize_schema(standardized)
        flat = flatten_standardized(standardized)
        conn = self._connect()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO ingested_logs (
                source_path, source_name, source_mtime, raw_content, standardized_json,
                timestamp_iso, tool_id, equipment_type, recipe_name, process_step,
                temperature_c, pressure_torr, rf_forward_w, rf_reflected_w, gas_flow_sccm,
                is_alarm, alarm_code, health_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_path,
                Path(source_path).name,
                source_mtime,
                raw_content,
                json.dumps(standardized, ensure_ascii=False, indent=2),
                flat["timestamp_iso"],
                flat["tool_id"],
                flat["equipment_type"],
                flat["recipe_name"],
                flat["process_step"],
                flat["temperature_c"],
                flat["pressure_torr"],
                flat["rf_forward_w"],
                flat["rf_reflected_w"],
                flat["gas_flow_sccm"],
                1 if flat["is_alarm"] else 0 if flat["is_alarm"] is not None else None,
                flat["alarm_code"],
                flat["health_score"],
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def list_logs(self, limit: int = 50) -> List[sqlite3.Row]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM ingested_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return rows

    def get_log(self, log_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        conn = self._connect()
        if log_id is None:
            row = conn.execute("SELECT * FROM ingested_logs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = conn.execute("SELECT * FROM ingested_logs WHERE id = ?", (log_id,)).fetchone()
        conn.close()
        return row

    def clear_logs(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM ingested_logs")
            conn.commit()

    def get_logs_by_ids(self, log_ids: List[int]) -> List[sqlite3.Row]:
        if not log_ids:
            return []
        placeholders = ", ".join("?" for _ in log_ids)
        conn = self._connect()
        rows = conn.execute(
            f"SELECT * FROM ingested_logs WHERE id IN ({placeholders}) ORDER BY id DESC",
            log_ids,
        ).fetchall()
        conn.close()
        return rows

    def delete_logs(self, log_ids: List[int]) -> int:
        if not log_ids:
            return 0
        placeholders = ", ".join("?" for _ in log_ids)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"DELETE FROM ingested_logs WHERE id IN ({placeholders})", log_ids)
            conn.commit()
            return cursor.rowcount


class LogIngestionPipeline:
    def __init__(self, watch_dir: str, db_path: str) -> None:
        self.watch_dir = Path(watch_dir)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.repo = LogRepository(db_path)
        self.ai = AIStandardizer()
        self.generator = AdvancedSemiLogGenerator(output_dir=str(self.watch_dir))

    def scan_once(self) -> int:
        ingested = 0
        for path in sorted(self.watch_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            mtime = path.stat().st_mtime
            if self.repo.has_file_version(str(path.resolve()), mtime):
                continue
            raw_content, standardized = self.process_file(path)
            self.repo.insert_log(str(path.resolve()), mtime, raw_content, standardized)
            ingested += 1
        return ingested

    def process_file(self, path: Path) -> tuple[str, Dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            raw_text = path.read_text(encoding="utf-8")
            return raw_text, parse_vendor_a_json(json.loads(raw_text))
        if suffix == ".xml":
            raw_text = path.read_text(encoding="utf-8")
            return raw_text, parse_vendor_b_xml(raw_text)
        if suffix == ".csv":
            raw_text = path.read_text(encoding="utf-8")
            return raw_text, parse_vendor_c_csv(raw_text)
        if suffix == ".log":
            raw_text = path.read_text(encoding="utf-8")
            return raw_text, self.ai.standardize_text(raw_text)
        if suffix == ".txt":
            raw_text = path.read_text(encoding="utf-8")
            return raw_text, self.ai.standardize_text(raw_text)
        if suffix == ".bin":
            raw_bytes = path.read_bytes()
            raw_text = format_binary_for_display(raw_bytes)
            return raw_text, parse_vendor_e_binary(raw_bytes)
        if suffix == ".parquet":
            raw_text, standardized = parse_vendor_h_parquet(path)
            return raw_text, standardized
        return "", UNIVERSAL_SCHEMA_EXAMPLE

    def generate_requested_files(self, counts: Dict[str, int]) -> int:
        generated = 0
        for vendor_code, count in counts.items():
            for _ in range(max(0, int(count or 0))):
                self._generate_vendor_file(vendor_code.upper())
                generated += 1
        if generated:
            self.scan_once()
        return generated

    def delete_logs(self, log_ids: List[int]) -> int:
        rows = self.repo.get_logs_by_ids(log_ids)
        for row in rows:
            source_path = Path(row["source_path"])
            try:
                if source_path.exists():
                    source_path.unlink()
            except OSError:
                pass
        return self.repo.delete_logs(log_ids)

    def _generate_vendor_file(self, vendor_code: str) -> Path:
        unique_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

        if vendor_code == "A":
            path = self.watch_dir / f"vendor_a_{unique_id}.json"
            path.write_text(json.dumps(self.generator.generate_vendor_a_complex(), indent=2), encoding="utf-8")
            return path
        if vendor_code == "B":
            path = self.watch_dir / f"vendor_b_{unique_id}.xml"
            data = self.generator.generate_vendor_b_xml()
            root = ET.Element("ProcessData")
            for key, value in data.items():
                child = ET.SubElement(root, key)
                child.text = str(value)
            path.write_text(minidom_pretty(ET.tostring(root, encoding="unicode")), encoding="utf-8")
            return path
        if vendor_code == "C":
            path = self.watch_dir / f"vendor_c_{unique_id}.csv"
            rows = self.generator.generate_vendor_c_timeseries(num_rows=30)
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            return path
        if vendor_code == "D":
            path = self.watch_dir / f"vendor_d_{unique_id}.log"
            path.write_text(self.generator.generate_vendor_d_text(), encoding="utf-8")
            return path
        if vendor_code == "E":
            path = self.watch_dir / f"vendor_e_{unique_id}.bin"
            path.write_bytes(self.generator.generate_vendor_e_binary())
            return path
        if vendor_code == "F":
            path = self.watch_dir / f"vendor_f_{unique_id}.log"
            path.write_text(self.generator.generate_syslog(), encoding="utf-8")
            return path
        if vendor_code == "G":
            path = self.watch_dir / f"vendor_g_{unique_id}.txt"
            path.write_text(self.generator.generate_key_value(), encoding="utf-8")
            return path
        if vendor_code == "H":
            path = self.watch_dir / f"vendor_h_{unique_id}.parquet"
            df = self.generator.generate_vendor_h_parquet(num_rows=1000)
            df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
            return path
        raise ValueError(f"Unsupported vendor code: {vendor_code}")


class FilePollWatcher:
    def __init__(self, pipeline: LogIngestionPipeline, interval_seconds: float = 1.0) -> None:
        self.pipeline = pipeline
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.pipeline.scan_once()
            except Exception:
                pass
            time.sleep(self.interval_seconds)


def normalize_schema(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = data or {}
    result = json.loads(json.dumps(UNIVERSAL_SCHEMA_EXAMPLE))
    for key, default_value in result.items():
        incoming = data.get(key) if isinstance(data, dict) else None
        if isinstance(default_value, dict):
            if isinstance(incoming, dict):
                result[key].update(incoming)
        elif incoming is not None:
            result[key] = incoming
    if not isinstance(result.get("vendor_metrics"), dict):
        result["vendor_metrics"] = {}
    return result


def flatten_standardized(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp_iso": data["base_info"]["timestamp_iso"],
        "tool_id": data["base_info"]["tool_id"],
        "equipment_type": data["base_info"]["equipment_type"],
        "recipe_name": data["base_info"]["recipe_name"],
        "process_step": data["base_info"]["process_step"],
        "temperature_c": data["normalized_metrics"]["temperature_c"],
        "pressure_torr": data["normalized_metrics"]["pressure_torr"],
        "rf_forward_w": data["normalized_metrics"]["rf_forward_w"],
        "rf_reflected_w": data["normalized_metrics"]["rf_reflected_w"],
        "gas_flow_sccm": data["normalized_metrics"]["gas_flow_sccm"],
        "is_alarm": data["health_status"]["is_alarm"],
        "alarm_code": data["health_status"]["alarm_code"],
        "health_score": data["health_status"]["health_score"],
    }


def gemini_response_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "base_info": {"type": "object"},
            "process_context": {"type": "object"},
            "event_context": {"type": "object"},
            "normalized_metrics": {"type": "object"},
            "health_status": {"type": "object"},
            "vendor_metrics": {"type": "object"},
        },
        "required": ["base_info", "process_context", "event_context", "normalized_metrics", "health_status", "vendor_metrics"],
        "additionalProperties": False,
    }


def extract_gemini_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response did not include candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text_parts = [part.get("text", "") for part in parts if isinstance(part, dict) and "text" in part]
    content = "".join(text_parts).strip()
    if not content:
        raise ValueError("Gemini response did not include text parts.")
    return content


def parse_vendor_a_json(data: Dict[str, Any]) -> Dict[str, Any]:
    gas_panel = data.get("Measurements", {}).get("GasPanel_MFC_sccm", {})
    rf_system = data.get("Measurements", {}).get("RF_System", {})
    temps = data.get("Measurements", {}).get("Temperature_C", {})
    vacuum = data.get("Measurements", {}).get("VacuumSystem", {})
    alarms = data.get("HardwareAlarms", {}).get("ActiveAlarms", [])
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": data.get("EventHeader", {}).get("Timestamp"),
                "tool_id": data.get("EventHeader", {}).get("ToolID"),
                "equipment_type": "ETCH",
                "recipe_name": data.get("LotContext", {}).get("RecipeName"),
                "process_step": data.get("LotContext", {}).get("ProcessStep"),
            },
            "process_context": {
                "lot_id": data.get("LotContext", {}).get("LotID"),
                "wafer_id": data.get("LotContext", {}).get("WaferID"),
                "slot_id": data.get("LotContext", {}).get("SlotID"),
                "step_time_sec": data.get("LotContext", {}).get("StepTimeSec"),
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "A",
                "source_format": "json",
                "event_id": data.get("EventHeader", {}).get("EventID"),
                "severity": "WARNING" if alarms else "INFO",
                "event_class": "process_alarm" if alarms else "process_event",
                "raw_message": alarms[0] if alarms else "NORMAL_OPERATION",
                "status": data.get("EventHeader", {}).get("SoftwareVersion"),
                "interlock_status": data.get("HardwareAlarms", {}).get("InterlockStatus"),
            },
            "normalized_metrics": {
                "temperature_c": temps.get("ESC_Temp"),
                "pressure_torr": safe_divide(vacuum.get("ChamberPressure_mTorr"), 1000),
                "rf_forward_w": rf_system.get("SourcePower_W"),
                "rf_reflected_w": rf_system.get("SourceReflected_W"),
                "gas_flow_sccm": gas_panel.get("CF4"),
                "bias_power_w": rf_system.get("BiasPower_W"),
                "bias_reflected_w": rf_system.get("BiasReflected_W"),
                "foreline_pressure_torr": None,
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": None,
                "wall_temp_c": temps.get("Wall_Temp"),
                "showerhead_temp_c": temps.get("Showerhead_Temp"),
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": vacuum.get("ThrottleValve_Angle"),
                "turbo_pump_speed_rpm": vacuum.get("TurboPump_Speed_RPM"),
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": bool(alarms),
                "alarm_code": first_or_default(alarms, "NONE"),
                "health_score": 84 if alarms else 98,
            },
            "vendor_metrics": {
                "software_version": data.get("EventHeader", {}).get("SoftwareVersion"),
                "match_network": rf_system.get("MatchNetwork", {}),
                "gas_panel_mfc_sccm": gas_panel,
            },
        }
    )


def parse_vendor_b_xml(raw_text: str) -> Dict[str, Any]:
    root = ET.fromstring(raw_text)
    values = {child.tag: child.text for child in root}
    heater_zones = {
        "zone_1_c": to_float(values.get("HEATER_ZONE_1_C")),
        "zone_2_c": to_float(values.get("HEATER_ZONE_2_C")),
        "zone_3_c": to_float(values.get("HEATER_ZONE_3_C")),
        "zone_4_c": to_float(values.get("HEATER_ZONE_4_C")),
    }
    gas_channels = {
        "sih4_sccm": to_int(values.get("GAS_SIH4_SCCM")),
        "n2o_sccm": to_int(values.get("GAS_N2O_SCCM")),
        "n2_sccm": to_int(values.get("GAS_N2_SCCM")),
    }
    is_alarm = values.get("ALARM_ID") not in {None, "0", "NONE"}
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": parse_dt(values.get("LOG_TIME")),
                "tool_id": values.get("EQUIPMENT_NAME"),
                "equipment_type": "CVD",
                "recipe_name": values.get("RECIPE"),
                "process_step": to_int(values.get("STEP")),
            },
            "process_context": {
                "lot_id": values.get("LOT_ID"),
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "B",
                "source_format": "xml",
                "event_id": values.get("ALARM_ID"),
                "severity": "ERROR" if is_alarm else "INFO",
                "event_class": "heater_alarm" if is_alarm else "process_snapshot",
                "raw_message": values.get("ALARM_TEXT"),
                "status": None,
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": average(list(heater_zones.values())),
                "pressure_torr": to_float(values.get("CHAMBER_PRESS_TORR")),
                "rf_forward_w": to_int(values.get("RF_HIGH_FREQ_W")),
                "rf_reflected_w": to_int(values.get("RF_LOW_FREQ_W")),
                "gas_flow_sccm": gas_channels["sih4_sccm"],
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": to_float(values.get("FORELINE_PRESS_TORR")),
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": None,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": average(list(heater_zones.values())),
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": to_int(values.get("SPINDLE_SPEED_RPM")),
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": values.get("ALARM_TEXT") or values.get("ALARM_ID") or "NONE",
                "health_score": 88 if is_alarm else 97,
            },
            "vendor_metrics": {
                "heater_zones_c": heater_zones,
                "gas_channels_sccm": gas_channels,
            },
        }
    )


def parse_vendor_c_csv(raw_text: str) -> Dict[str, Any]:
    reader = csv.DictReader(raw_text.splitlines())
    rows = list(reader)
    last = rows[-1] if rows else {}
    align_x = to_int(last.get("Alignment_Error_X_nm"))
    align_y = to_int(last.get("Alignment_Error_Y_nm"))
    is_alarm = abs(align_x or 0) > 4 or abs(align_y or 0) > 4
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": parse_dt(last.get("Timestamp")),
                "tool_id": last.get("Tool"),
                "equipment_type": "LITHO",
                "recipe_name": last.get("Reticle_ID"),
                "process_step": to_int(last.get("StepID")),
            },
            "process_context": {
                "lot_id": last.get("Lot"),
                "wafer_id": last.get("Wafer"),
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "C",
                "source_format": "csv",
                "event_id": None,
                "severity": "WARN" if is_alarm else "INFO",
                "event_class": "alignment_drift" if is_alarm else "process_snapshot",
                "raw_message": "ALIGNMENT_DRIFT" if is_alarm else "LITHO_STEP_SAMPLE",
                "status": None,
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": to_float(last.get("Lens_Temp_C")),
                "pressure_torr": None,
                "rf_forward_w": None,
                "rf_reflected_w": None,
                "gas_flow_sccm": None,
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": None,
                "helium_pressure_psi": None,
                "laser_power_mw": to_float(last.get("Laser_Power_mW")),
                "lens_temp_c": to_float(last.get("Lens_Temp_C")),
                "pump_temp_c": None,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": to_float(last.get("Dose_mJ_cm2")),
                "focus_offset_nm": to_int(last.get("Focus_Offset_nm")),
                "alignment_error_x_nm": align_x,
                "alignment_error_y_nm": align_y,
                "leveling_z_um": to_float(last.get("Leveling_Z_um")),
                "stage_x_pos_mm": to_float(last.get("Stage_X_Pos_mm")),
                "stage_y_pos_mm": to_float(last.get("Stage_Y_Pos_mm")),
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": "ALIGNMENT_DRIFT" if is_alarm else "NONE",
                "health_score": 93,
            },
            "vendor_metrics": {},
        }
    )


def classify_syslog_message(message: Optional[str], severity: Optional[str]) -> tuple[str, bool, str, int]:
    msg = (message or "").lower()
    sev = (severity or "").upper()
    if "interlock" in msg:
        return "critical_fault", True, "HEATER_POWER_SUPPLY_INTERLOCK", 55 if sev == "CRITICAL" else 70
    if "fluctuation" in msg:
        return "warning", True, "COOLING_WATER_FLOW_FLUCTUATION", 84
    if "baseline pressure" in msg:
        return "process_event", False, "VACUUM_BASELINE_REACHED", 98
    if "alignment completed" in msg:
        return "process_event", False, "ALIGNMENT_COMPLETED", 99
    if "state changed to open" in msg:
        return "state_change", False, "GATE_VALVE_OPEN", 97
    return ("critical_fault" if sev == "CRITICAL" else "warning"), sev in {"WARN", "ERROR", "CRITICAL"}, normalized_alarm_code(message), 82


def extract_syslog_metrics(message: Optional[str]) -> tuple[Optional[str], Dict[str, Any]]:
    if not message:
        return message, {}
    parts = message.split("|", 1)
    if len(parts) == 1:
        return message.strip(), {}
    base_message = parts[0].strip()
    metrics_blob = parts[1].strip()
    metrics = {}
    for token in metrics_blob.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        metrics[key.strip()] = value.strip()
    return base_message, metrics


def parse_vendor_d_text(raw_text: str) -> Dict[str, Any]:
    ts_match = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\.\d{3})", raw_text)
    machine_match = re.search(r"Machine:([A-Za-z0-9_-]+)", raw_text)
    current_scan = extract_last_float(raw_text, r"Current scan time:\s*([0-9.]+)")
    required_scan = extract_last_float(raw_text, r"required minimum scan time:\s*([0-9.]+)")
    bracket_alarm = re.findall(r"\[([^\]]+)\]", raw_text)
    event_type = "warning" if "SYSTEM WARNING" in raw_text else "event"
    alarm_code = bracket_alarm[-1].split(":")[-1] if bracket_alarm else ("DW_SCAN_TIME_SHORT" if current_scan else "ER_OFF_DEFAULT")
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": to_iso_compact(ts_match.group(1), "%d/%m/%Y %H:%M:%S.%f") if ts_match else None,
                "tool_id": machine_match.group(1) if machine_match else None,
                "equipment_type": "LITHO",
                "recipe_name": "OASIS_LIGHT_MEASUREMENT" if "OASIS" in raw_text else None,
                "process_step": None,
            },
            "process_context": {
                "lot_id": None,
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "D",
                "source_format": "log",
                "event_id": alarm_code,
                "severity": "WARN" if event_type == "warning" else "INFO",
                "event_class": "scan_timing_warning" if current_scan else "state_event",
                "raw_message": last_nonempty_line(raw_text),
                "status": event_type.upper(),
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": None,
                "pressure_torr": None,
                "rf_forward_w": None,
                "rf_reflected_w": None,
                "gas_flow_sccm": None,
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": None,
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": None,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": current_scan,
                "required_scan_time_ms": required_scan,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": event_type == "warning",
                "alarm_code": alarm_code,
                "health_score": 72 if event_type == "warning" else 90,
            },
            "vendor_metrics": {
                "deactivate_targets": re.findall(r"DEACTIVATE:\s*([A-Za-z0-9-]+)", raw_text),
            },
        }
    )


def parse_vendor_f_syslog(raw_text: str) -> Dict[str, Any]:
    lines = [line for line in raw_text.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    match = re.search(r"<(\d+)>(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(\S+)\s+SEMI_APP:\s+\[([^\]]+)\]\s+(.*)", last)
    timestamp = parse_dt(match.group(2)) if match else None
    priority = to_int(match.group(1)) if match else None
    tool_id = match.group(3) if match else None
    severity = match.group(4) if match else None
    raw_message = match.group(5) if match else None
    message, extracted_metrics = extract_syslog_metrics(raw_message)
    event_class, is_alarm, alarm_code, health_score = classify_syslog_message(message, severity)
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": timestamp,
                "tool_id": tool_id,
                "equipment_type": infer_equipment_type(tool_id),
                "recipe_name": None,
                "process_step": None,
            },
            "process_context": {
                "lot_id": None,
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "F",
                "source_format": "syslog",
                "event_id": alarm_code,
                "severity": severity,
                "event_class": event_class,
                "raw_message": message,
                "status": None,
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": to_float(extracted_metrics.get("temp_c")),
                "pressure_torr": to_float(extracted_metrics.get("pressure_torr")),
                "rf_forward_w": to_int(extracted_metrics.get("rf_forward_w")),
                "rf_reflected_w": None,
                "gas_flow_sccm": to_int(extracted_metrics.get("flow_sccm")),
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": to_float(extracted_metrics.get("foreline_torr")),
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": None,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
                "alignment_error_x_nm": to_int(extracted_metrics.get("alignment_error_x_nm")),
                "alignment_error_y_nm": to_int(extracted_metrics.get("alignment_error_y_nm")),
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": alarm_code if is_alarm else "NONE",
                "health_score": health_score,
            },
            "vendor_metrics": {
                "syslog_priority": priority,
                "host": tool_id,
                "message_text": message,
                "valve_position_pct": to_int(extracted_metrics.get("valve_position_pct")),
                "extracted_metrics": extracted_metrics,
            },
        }
    )


def parse_vendor_g_key_value(raw_text: str) -> Dict[str, Any]:
    data: Dict[str, str] = {}
    for line in raw_text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    error_count = to_int(data.get("error_count"))
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": datetime.utcnow().isoformat() + "Z",
                "tool_id": data.get("system_id"),
                "equipment_type": infer_equipment_type(data.get("system_id")),
                "recipe_name": data.get("last_recipe"),
                "process_step": None,
            },
            "process_context": {
                "lot_id": None,
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": to_int(data.get("uptime_seconds")),
            },
            "event_context": {
                "source_vendor": "G",
                "source_format": "kv",
                "event_id": None,
                "severity": "WARN" if (error_count or 0) > 0 else "INFO",
                "event_class": "state_dump",
                "raw_message": raw_text,
                "status": data.get("status"),
                "interlock_status": data.get("gas_leak_test"),
            },
            "normalized_metrics": {
                "temperature_c": to_float(data.get("chamber_temp_c")),
                "pressure_torr": to_float(data.get("foreline_pressure_torr")),
                "rf_forward_w": None,
                "rf_reflected_w": None,
                "gas_flow_sccm": to_int(data.get("mfc_actual_sccm")),
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": to_float(data.get("foreline_pressure_torr")),
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": to_float(data.get("pump_temp_c")),
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": (error_count or 0) > 0,
                "alarm_code": "ERROR_COUNT_NONZERO" if (error_count or 0) > 0 else "NONE",
                "health_score": 95 if (error_count or 0) == 0 else 80,
            },
            "vendor_metrics": {
                "mfc_setpoint_sccm": to_int(data.get("mfc_setpoint_sccm")),
                "gas_leak_test": data.get("gas_leak_test"),
            },
        }
    )


def parse_vendor_e_binary(raw_bytes: bytes) -> Dict[str, Any]:
    fmt = "i I B d d d H f f f B I"
    size = struct.calcsize(fmt)
    payload = raw_bytes[:size]
    values = struct.unpack(fmt, payload)
    step_id, recipe_hash, status_bits, fwd_power, ref_power, bias_v, esc_voltage, throttle_pos, mfc_1_flow, mfc_2_flow, pump_temp, cycle_count = values
    is_alarm = (status_bits & 0x80) != 0
    return normalize_schema(
        {
            "base_info": {
                "timestamp_iso": datetime.utcnow().isoformat() + "Z",
                "tool_id": "ETCH-BIN-01",
                "equipment_type": "ETCH",
                "recipe_name": f"HASH-{recipe_hash}",
                "process_step": step_id,
            },
            "process_context": {
                "lot_id": None,
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "E",
                "source_format": "binary",
                "event_id": f"STATUS_{status_bits}",
                "severity": "ERROR" if is_alarm else "INFO",
                "event_class": "binary_snapshot",
                "raw_message": None,
                "status": bin(status_bits),
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": pump_temp,
                "pressure_torr": round(throttle_pos / 1000, 4),
                "rf_forward_w": int(fwd_power),
                "rf_reflected_w": int(ref_power),
                "gas_flow_sccm": int(mfc_1_flow + mfc_2_flow),
                "bias_power_w": int(abs(bias_v)),
                "bias_reflected_w": None,
                "foreline_pressure_torr": None,
                "helium_pressure_psi": None,
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": pump_temp,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": round(throttle_pos, 3),
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": "BIN_ERR" if is_alarm else "NONE",
                "health_score": 90 if is_alarm else 96,
            },
            "vendor_metrics": {
                "status_bits": status_bits,
                "bias_voltage_v": round(bias_v, 3),
                "esc_voltage_v": esc_voltage,
                "mfc_1_flow_sccm": round(mfc_1_flow, 3),
                "mfc_2_flow_sccm": round(mfc_2_flow, 3),
                "cycle_count": cycle_count,
            },
        }
    )


def parse_vendor_h_parquet(path: Path) -> tuple[str, Dict[str, Any]]:
    df = pd.read_parquet(path)
    last = df.iloc[-1]
    anomaly_count = int(df["is_anomaly"].sum()) if "is_anomaly" in df else 0
    raw_preview = df.tail(5).to_string(index=False)
    return raw_preview, normalize_schema(
        {
            "base_info": {
                "timestamp_iso": getattr(last["timestamp"], "isoformat", lambda: str(last["timestamp"]))(),
                "tool_id": last.get("sensor_id"),
                "equipment_type": "METROLOGY",
                "recipe_name": "HE_MONITOR",
                "process_step": 1,
            },
            "process_context": {
                "lot_id": None,
                "wafer_id": None,
                "slot_id": None,
                "step_time_sec": None,
                "uptime_seconds": None,
            },
            "event_context": {
                "source_vendor": "H",
                "source_format": "parquet",
                "event_id": None,
                "severity": "WARN" if bool(last.get("is_anomaly")) else "INFO",
                "event_class": "sensor_snapshot",
                "raw_message": None,
                "status": None,
                "interlock_status": None,
            },
            "normalized_metrics": {
                "temperature_c": to_float(last.get("backside_temp_c")),
                "pressure_torr": None,
                "rf_forward_w": 0,
                "rf_reflected_w": 0,
                "gas_flow_sccm": to_int(last.get("flow_rate_sccm")),
                "bias_power_w": None,
                "bias_reflected_w": None,
                "foreline_pressure_torr": None,
                "helium_pressure_psi": to_float(last.get("helium_pressure_psi")),
                "laser_power_mw": None,
                "lens_temp_c": None,
                "pump_temp_c": None,
                "wall_temp_c": None,
                "showerhead_temp_c": None,
                "heater_zone_avg_c": None,
                "dose_mj_cm2": None,
                "focus_offset_nm": None,
                "alignment_error_x_nm": None,
                "alignment_error_y_nm": None,
                "leveling_z_um": None,
                "stage_x_pos_mm": None,
                "stage_y_pos_mm": None,
                "scan_time_ms": None,
                "required_scan_time_ms": None,
                "throttle_valve_angle_deg": None,
                "turbo_pump_speed_rpm": None,
                "spindle_speed_rpm": None,
            },
            "health_status": {
                "is_alarm": bool(last.get("is_anomaly")),
                "alarm_code": "PRESSURE_SPIKE" if bool(last.get("is_anomaly")) else "NONE",
                "health_score": 84 if bool(last.get("is_anomaly")) else 98,
            },
            "vendor_metrics": {
                "sensor_id": last.get("sensor_id"),
                "anomaly_count": anomaly_count,
            },
        }
    )


def format_binary_for_display(raw_bytes: bytes) -> str:
    hex_text = binascii.hexlify(raw_bytes).decode("ascii")
    grouped = " ".join(hex_text[i : i + 2] for i in range(0, len(hex_text), 2))
    return f"Binary length: {len(raw_bytes)} bytes\nHEX:\n{grouped}"


def minidom_pretty(xml_text: str) -> str:
    return minidom.parseString(xml_text).toprettyxml(indent="    ")


def parse_dt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def infer_equipment_type(tool_id: Optional[str]) -> Optional[str]:
    if not tool_id:
        return None
    text = str(tool_id).upper()
    if "ETCH" in text or "CHAMBER" in text or "PVD" in text:
        return "ETCH"
    if "CVD" in text or "FURNACE" in text or "DEP" in text or "DIFFUSION" in text:
        return "CVD"
    if "LITHO" in text or "SCANNER" in text or "MASK" in text or "OASIS" in text:
        return "LITHO"
    if "METROLOGY" in text or "SNS" in text:
        return "METROLOGY"
    return None


def first_or_default(values: List[Any], default: Any) -> Any:
    return values[0] if values else default


def safe_divide(value: Optional[float], divisor: float) -> Optional[float]:
    if value is None:
        return None
    return round(float(value) / divisor, 4)


def to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def average(values: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 3)


def sum_ignore_none(values: List[Optional[int]]) -> Optional[int]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return int(sum(clean))


def round_or_none(value: Any) -> Optional[int]:
    num = to_float(value)
    if num is None:
        return None
    return round(num)


def normalized_alarm_code(raw_text: Optional[str]) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", (raw_text or "").strip()).strip("_").upper()
    return clean or "UNKNOWN"


def extract_last_float(raw_text: str, pattern: str) -> Optional[float]:
    matches = re.findall(pattern, raw_text, flags=re.IGNORECASE)
    if not matches:
        return None
    return to_float(matches[-1])


def last_nonempty_line(raw_text: str) -> Optional[str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines[-1] if lines else None


def to_iso_compact(value: str, fmt: str) -> Optional[str]:
    try:
        normalized = re.sub(r"\s+", " ", value.strip())
        return datetime.strptime(normalized, fmt).isoformat()
    except Exception:
        return None
