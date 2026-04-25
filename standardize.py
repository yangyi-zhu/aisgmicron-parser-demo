import csv
import glob
import json
import os
import re
import struct
from copy import deepcopy
from datetime import datetime

import pandas as pd
import xmltodict

try:
    from google import genai
except ImportError:
    genai = None


MODEL_ID = "gemini-2.5-flash-lite"
CONFIDENCE_THRESHOLD = float(os.getenv("STANDARDIZE_CONFIDENCE_THRESHOLD", "0.8"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(SCRIPT_DIR, "standardize_mappings.json")

UNIVERSAL_SCHEME = {
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

DEFAULT_RULES = {
    "equipment_hints": {
        "alignment": "LITHO",
        "backside": "METROLOGY",
        "cf4": "ETCH",
        "chf3": "ETCH",
        "cl2": "ETCH",
        "cvd": "CVD",
        "deposition": "CVD",
        "diffusion": "CVD",
        "etch": "ETCH",
        "furnace": "CVD",
        "heater": "CVD",
        "helium": "METROLOGY",
        "lens_temp_c": "LITHO",
        "litho": "LITHO",
        "mask": "LITHO",
        "metrology": "METROLOGY",
        "mfc": "ETCH",
        "n2o": "CVD",
        "oasis": "LITHO",
        "reticle": "LITHO",
        "scan": "LITHO",
        "sensor": "METROLOGY",
        "sih4": "CVD",
        "vacuum": "ETCH",
    },
    "alarm_terms": {
        "cooling water flow rate fluctuation detected": "COOLING_WATER_FLOW_FLUCTUATION",
        "dwfmxoa_light_timing_warning": "DWFMxOA_LIGHT_TIMING_WARNING",
        "gate valve state changed to open": "GATE_VALVE_OPEN",
        "heater power supply interlock triggered": "HEATER_POWER_SUPPLY_INTERLOCK",
        "pressure spike": "PRESSURE_SPIKE",
        "scan time too short to program oasis light trigger": "DW_SCAN_TIME_SHORT",
        "vacuum pump reached baseline pressure": "VACUUM_BASELINE_REACHED",
        "wafer centering alignment completed": "ALIGNMENT_COMPLETED",
        "zone 4 temp low": "ERR-774-HEATER",
    },
    "status_terms": {
        "alarm": True,
        "critical": True,
        "error": True,
        "interlock": True,
        "warn": True,
        "warning": True,
    },
}

AI_FALLBACK_PROMPT = """
You are a semiconductor data engineer converting raw equipment logs into a standard JSON object.

Return JSON only in this exact format:
{
  "standardized": {
    "base_info": {},
    "process_context": {},
    "event_context": {},
    "normalized_metrics": {},
    "health_status": {},
    "vendor_metrics": {}
  },
  "learned_mappings": {
    "equipment_hints": {"term": "equipment_type"},
    "alarm_terms": {"raw_term": "normalized_alarm_code"},
    "status_terms": {"raw_term": true},
    "metric_aliases": {"standard_metric": ["raw_metric_name"]}
  }
}

Only add learned_mappings for terms that are clearly reusable in future logs.
Current mappings:
{rules_json}

Deterministic draft:
{draft_json}

Raw log:
{raw_text}
"""


def base_record():
    return deepcopy(UNIVERSAL_SCHEME)


def deep_merge(base, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_rules():
    rules = deepcopy(DEFAULT_RULES)
    if os.path.exists(RULES_PATH):
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        deep_merge(rules, stored)
    return rules


def save_rules(rules):
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=4, sort_keys=True)


def normalized_alarm_code(raw_text):
    clean = re.sub(r"[^A-Za-z0-9]+", "_", (raw_text or "").strip()).strip("_").upper()
    return clean or "UNKNOWN"


def infer_equipment_type(raw_text, rules):
    haystack = (raw_text or "").lower()
    for term, equipment_type in rules.get("equipment_hints", {}).items():
        if term.lower() in haystack:
            return equipment_type
    return None


def infer_alarm_code(raw_text, rules):
    haystack = (raw_text or "").lower()
    for term, code in rules.get("alarm_terms", {}).items():
        if term.lower() in haystack:
            return code
    return None


def infer_alarm_state(raw_text, rules):
    haystack = (raw_text or "").lower()
    for term, is_alarm in rules.get("status_terms", {}).items():
        if term.lower() in haystack:
            return bool(is_alarm)
    return None


def to_iso(ts_text, fmt=None):
    if not ts_text:
        return None
    normalized_text = re.sub(r"\s+", " ", str(ts_text).strip())
    if fmt:
        return datetime.strptime(normalized_text, fmt).isoformat()
    return datetime.fromisoformat(normalized_text).isoformat()


def section_completeness(section):
    if not section:
        return 0.0
    values = section.values()
    populated = sum(value is not None and value != {} for value in values)
    return populated / len(section)


def metric_completeness(record):
    return section_completeness(record["normalized_metrics"])


def evaluate_confidence(record, base_score):
    bounded_base = max(0.0, min(1.0, base_score))
    section_scores = [
        section_completeness(record["base_info"]),
        section_completeness(record["process_context"]),
        section_completeness(record["event_context"]),
        metric_completeness(record),
        section_completeness(record["health_status"]),
    ]
    score = (bounded_base * 0.4) + (sum(section_scores) / len(section_scores) * 0.6)
    return round(min(score, 1.0), 4)


def get_genai_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or genai is None:
        return None
    return genai.Client(api_key=api_key)


def get_ai_standardization(raw_text, deterministic_record, rules):
    client = get_genai_client()
    if client is None:
        return None, None

    prompt = (
        AI_FALLBACK_PROMPT.replace("{rules_json}", json.dumps(rules, indent=2, sort_keys=True))
        .replace("{draft_json}", json.dumps(deterministic_record, indent=2))
        .replace("{raw_text}", raw_text)
    )

    try:
        response = client.models.generate_content(model=MODEL_ID, contents=prompt)
        clean_text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        payload = json.loads(clean_text)
    except Exception as exc:
        print(f"AI mapping error: {exc}")
        return None, None

    if "standardized" in payload:
        return payload.get("standardized"), payload.get("learned_mappings")
    return payload, None


def maybe_learn_from_ai(rules, learned_mappings):
    if not learned_mappings:
        return False
    before = json.dumps(rules, sort_keys=True)
    deep_merge(rules, learned_mappings)
    return before != json.dumps(rules, sort_keys=True)


def classify_syslog_message(message, severity):
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


def parse_vendor_a_json(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    gas_panel = data["Measurements"]["GasPanel_MFC_sccm"]
    rf_system = data["Measurements"]["RF_System"]
    temps = data["Measurements"]["Temperature_C"]
    vacuum = data["Measurements"]["VacuumSystem"]
    alarms = data["HardwareAlarms"]["ActiveAlarms"]
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": data["EventHeader"]["Timestamp"],
                "tool_id": data["EventHeader"]["ToolID"],
                "equipment_type": "ETCH",
                "recipe_name": data["LotContext"]["RecipeName"],
                "process_step": data["LotContext"]["ProcessStep"],
            },
            "process_context": {
                "lot_id": data["LotContext"]["LotID"],
                "wafer_id": data["LotContext"]["WaferID"],
                "slot_id": data["LotContext"]["SlotID"],
                "step_time_sec": data["LotContext"]["StepTimeSec"],
            },
            "event_context": {
                "source_vendor": "A",
                "source_format": "json",
                "event_id": data["EventHeader"]["EventID"],
                "severity": "WARNING" if alarms else "INFO",
                "event_class": "process_alarm" if alarms else "process_event",
                "raw_message": alarms[0] if alarms else "NORMAL_OPERATION",
                "status": data["EventHeader"]["SoftwareVersion"],
                "interlock_status": data["HardwareAlarms"]["InterlockStatus"],
            },
            "normalized_metrics": {
                "temperature_c": temps["ESC_Temp"],
                "pressure_torr": vacuum["ChamberPressure_mTorr"] / 1000,
                "rf_forward_w": rf_system["SourcePower_W"],
                "rf_reflected_w": rf_system["SourceReflected_W"],
                "gas_flow_sccm": gas_panel["CF4"],
                "bias_power_w": rf_system["BiasPower_W"],
                "bias_reflected_w": rf_system["BiasReflected_W"],
                "wall_temp_c": temps["Wall_Temp"],
                "showerhead_temp_c": temps["Showerhead_Temp"],
                "throttle_valve_angle_deg": vacuum["ThrottleValve_Angle"],
                "turbo_pump_speed_rpm": vacuum["TurboPump_Speed_RPM"],
            },
            "health_status": {
                "is_alarm": bool(alarms),
                "alarm_code": alarms[0] if alarms else "NONE",
                "health_score": 84 if alarms else 98,
            },
            "vendor_metrics": {
                "software_version": data["EventHeader"]["SoftwareVersion"],
                "match_network": rf_system["MatchNetwork"],
                "gas_panel_mfc_sccm": gas_panel,
            },
        },
    )
    return record, 0.92, None


def parse_vendor_b_xml(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = xmltodict.parse(f.read())["ProcessData"]
    heater_zones = {
        "zone_1_c": to_float(data["HEATER_ZONE_1_C"]),
        "zone_2_c": to_float(data["HEATER_ZONE_2_C"]),
        "zone_3_c": to_float(data["HEATER_ZONE_3_C"]),
        "zone_4_c": to_float(data["HEATER_ZONE_4_C"]),
    }
    gas_channels = {
        "sih4_sccm": to_int(data["GAS_SIH4_SCCM"]),
        "n2o_sccm": to_int(data["GAS_N2O_SCCM"]),
        "n2_sccm": to_int(data["GAS_N2_SCCM"]),
    }
    is_alarm = data.get("ALARM_ID") not in (None, "0", 0)
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": to_iso(data["LOG_TIME"], "%Y-%m-%d %H:%M:%S.%f"),
                "tool_id": data["EQUIPMENT_NAME"],
                "equipment_type": "CVD",
                "recipe_name": data["RECIPE"],
                "process_step": int(data["STEP"]),
            },
            "event_context": {
                "source_vendor": "B",
                "source_format": "xml",
                "event_id": data.get("ALARM_ID"),
                "severity": "ERROR" if is_alarm else "INFO",
                "event_class": "heater_alarm" if is_alarm else "process_snapshot",
                "raw_message": data.get("ALARM_TEXT"),
            },
            "normalized_metrics": {
                "temperature_c": average(list(heater_zones.values())),
                "pressure_torr": float(data["CHAMBER_PRESS_TORR"]),
                "rf_forward_w": int(float(data["RF_HIGH_FREQ_W"])),
                "rf_reflected_w": int(float(data["RF_LOW_FREQ_W"])),
                "gas_flow_sccm": int(float(data["GAS_SIH4_SCCM"])),
                "foreline_pressure_torr": to_float(data.get("FORELINE_PRESS_TORR")),
                "heater_zone_avg_c": average(list(heater_zones.values())),
                "spindle_speed_rpm": to_int(data.get("SPINDLE_SPEED_RPM")),
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": data.get("ALARM_TEXT") or data.get("ALARM_ID") or "NONE",
                "health_score": 88 if is_alarm else 97,
            },
            "vendor_metrics": {
                "heater_zones_c": heater_zones,
                "gas_channels_sccm": gas_channels,
            },
        },
    )
    return record, 0.9, None


def parse_vendor_c_csv(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    last_row = rows[-1]
    align_x = to_int(last_row.get("Alignment_Error_X_nm"))
    align_y = to_int(last_row.get("Alignment_Error_Y_nm"))
    is_alarm = abs(align_x or 0) > 4 or abs(align_y or 0) > 4
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": to_iso(last_row["Timestamp"], "%Y-%m-%d %H:%M:%S"),
                "tool_id": last_row["Tool"],
                "equipment_type": "LITHO",
                "recipe_name": last_row["Reticle_ID"],
                "process_step": int(last_row["StepID"]),
            },
            "process_context": {
                "lot_id": last_row.get("Lot"),
                "wafer_id": last_row.get("Wafer"),
            },
            "event_context": {
                "source_vendor": "C",
                "source_format": "csv",
                "severity": "WARN" if is_alarm else "INFO",
                "event_class": "alignment_drift" if is_alarm else "process_snapshot",
                "raw_message": "ALIGNMENT_DRIFT" if is_alarm else "LITHO_STEP_SAMPLE",
            },
            "normalized_metrics": {
                "temperature_c": to_float(last_row.get("Lens_Temp_C")),
                "laser_power_mw": to_float(last_row.get("Laser_Power_mW")),
                "lens_temp_c": to_float(last_row.get("Lens_Temp_C")),
                "dose_mj_cm2": to_float(last_row.get("Dose_mJ_cm2")),
                "focus_offset_nm": to_int(last_row.get("Focus_Offset_nm")),
                "alignment_error_x_nm": align_x,
                "alignment_error_y_nm": align_y,
                "leveling_z_um": to_float(last_row.get("Leveling_Z_um")),
                "stage_x_pos_mm": to_float(last_row.get("Stage_X_Pos_mm")),
                "stage_y_pos_mm": to_float(last_row.get("Stage_Y_Pos_mm")),
            },
            "health_status": {
                "is_alarm": is_alarm,
                "alarm_code": "ALIGNMENT_DRIFT" if is_alarm else "NONE",
                "health_score": 93,
            },
        },
    )
    return record, 0.84, None


def parse_vendor_d_text(raw_text, rules):
    record = base_record()
    ts_match = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\.\d{3})", raw_text)
    machine_match = re.search(r"Machine:([A-Za-z0-9_-]+)", raw_text)
    current_scan = extract_last_float(raw_text, r"Current scan time:\s*([0-9.]+)")
    required_scan = extract_last_float(raw_text, r"required minimum scan time:\s*([0-9.]+)")
    bracket_alarm = re.findall(r"\[([^\]]+)\]", raw_text)
    is_warning = "SYSTEM WARNING" in raw_text
    alarm_code = bracket_alarm[-1].split(":")[-1] if bracket_alarm else infer_alarm_code(raw_text, rules) or ("ER_OFF_DEFAULT" if "ER-OFF DEFAULT" in raw_text else "DW_SCAN_TIME_SHORT")
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": to_iso(ts_match.group(1), "%d/%m/%Y %H:%M:%S.%f") if ts_match else None,
                "tool_id": machine_match.group(1) if machine_match else None,
                "equipment_type": infer_equipment_type(raw_text, rules) or "LITHO",
                "recipe_name": "OASIS_LIGHT_MEASUREMENT" if "OASIS" in raw_text else None,
            },
            "event_context": {
                "source_vendor": "D",
                "source_format": "log",
                "event_id": alarm_code,
                "severity": "WARN" if is_warning else "INFO",
                "event_class": "scan_timing_warning" if current_scan else "state_event",
                "raw_message": last_nonempty_line(raw_text),
                "status": "WARNING" if is_warning else "EVENT",
            },
            "normalized_metrics": {
                "scan_time_ms": current_scan,
                "required_scan_time_ms": required_scan,
            },
            "health_status": {
                "is_alarm": is_warning,
                "alarm_code": alarm_code if is_warning else "NONE",
                "health_score": 72 if is_warning else 90,
            },
            "vendor_metrics": {
                "deactivate_targets": re.findall(r"DEACTIVATE:\s*([A-Za-z0-9-]+)", raw_text),
            },
        },
    )
    return record, 0.72, None


def parse_vendor_f_syslog(raw_text, rules):
    record = base_record()
    lines = [line for line in raw_text.splitlines() if line.strip()]
    last_line = lines[-1] if lines else ""
    match = re.search(
        r"<(\d+)>(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+([A-Za-z0-9_-]+)\s+\S+:\s+\[([A-Z]+)\]\s+(.+)",
        last_line,
    )
    priority = to_int(match.group(1)) if match else None
    level = match.group(4) if match else None
    tool_id = match.group(3) if match else None
    message = match.group(5) if match else raw_text
    event_class, is_alarm, alarm_code, health_score = classify_syslog_message(message, level)
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": to_iso(match.group(2)) if match else None,
                "tool_id": tool_id,
                "equipment_type": infer_equipment_type(tool_id or message, rules) or "ETCH",
            },
            "event_context": {
                "source_vendor": "F",
                "source_format": "syslog",
                "event_id": alarm_code,
                "severity": level,
                "event_class": event_class,
                "raw_message": message,
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
            },
        },
    )
    return record, 0.7, None


def parse_vendor_g_key_value(raw_text, rules):
    kv_pairs = {}
    for line in raw_text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            kv_pairs[key.strip()] = value.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            kv_pairs[key.strip()] = value.strip()
    error_count = int(float(kv_pairs.get("error_count", "0")))
    tool_id = kv_pairs.get("system_id")
    recipe_name = kv_pairs.get("last_recipe")
    equipment_type = infer_equipment_type(" ".join([tool_id or "", recipe_name or "", raw_text]), rules) or "ETCH"
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": datetime.now().isoformat(),
                "tool_id": tool_id,
                "equipment_type": equipment_type,
                "recipe_name": recipe_name,
            },
            "process_context": {
                "uptime_seconds": to_int(kv_pairs.get("uptime_seconds")),
            },
            "event_context": {
                "source_vendor": "G",
                "source_format": "kv",
                "severity": "WARN" if error_count > 0 else "INFO",
                "event_class": "state_dump",
                "raw_message": raw_text,
                "status": kv_pairs.get("status"),
                "interlock_status": kv_pairs.get("gas_leak_test"),
            },
            "normalized_metrics": {
                "temperature_c": float(kv_pairs["chamber_temp_c"]) if "chamber_temp_c" in kv_pairs else None,
                "gas_flow_sccm": int(float(kv_pairs["mfc_actual_sccm"])) if "mfc_actual_sccm" in kv_pairs else None,
            },
            "health_status": {
                "is_alarm": error_count > 0,
                "alarm_code": "SYSTEM_ERRORS_PRESENT" if error_count > 0 else "NONE",
                "health_score": max(0, 99 - (error_count * 5)),
            },
            "vendor_metrics": {
                "mfc_setpoint_sccm": to_int(kv_pairs.get("mfc_setpoint_sccm")),
                "gas_leak_test": kv_pairs.get("gas_leak_test"),
            },
        },
    )
    return record, 0.82, None


def parse_vendor_e_binary(file_path):
    with open(file_path, "rb") as f:
        fmt = "i I B d d d H f f f B I"
        size = struct.calcsize(fmt)
        data = f.read(size)
    res = struct.unpack(fmt, data)
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": datetime.now().isoformat() + "Z",
                "tool_id": "ETCH-BIN-01",
                "equipment_type": "ETCH",
                "recipe_name": f"RECIPE_{res[1]}",
                "process_step": res[0],
            },
            "event_context": {
                "source_vendor": "E",
                "source_format": "binary",
                "event_id": f"STATUS_{res[2]}",
                "severity": "ERROR" if (res[2] & 0x80) else "INFO",
                "event_class": "binary_snapshot",
                "status": bin(res[2]),
            },
            "normalized_metrics": {
                "temperature_c": float(res[10]),
                "pressure_torr": round(res[7] / 1000, 5),
                "rf_forward_w": int(res[3]),
                "rf_reflected_w": int(res[4]),
                "gas_flow_sccm": int(res[8] + res[9]),
                "bias_power_w": int(abs(res[5])),
                "pump_temp_c": float(res[10]),
                "throttle_valve_angle_deg": round(res[7], 3),
            },
            "health_status": {
                "is_alarm": (res[2] & 0x80) != 0,
                "alarm_code": "BIN_ERR" if (res[2] & 0x80) else "NONE",
                "health_score": 90 if (res[2] & 0x80) == 0 else 68,
            },
            "vendor_metrics": {
                "status_bits": res[2],
                "bias_voltage_v": round(res[5], 3),
                "esc_voltage_v": res[6],
                "mfc_1_flow_sccm": round(res[8], 3),
                "mfc_2_flow_sccm": round(res[9], 3),
                "cycle_count": res[11],
            },
        },
    )
    return record, 0.9, None


def parse_vendor_h_parquet(file_path):
    df = pd.read_parquet(file_path)
    last_row = df.iloc[-1]
    anomaly_count = int(df["is_anomaly"].sum()) if "is_anomaly" in df else 0
    record = base_record()
    deep_merge(
        record,
        {
            "base_info": {
                "timestamp_iso": last_row["timestamp"].isoformat(),
                "tool_id": last_row["sensor_id"],
                "equipment_type": "METROLOGY",
                "recipe_name": "HE_MONITOR",
                "process_step": 1,
            },
            "event_context": {
                "source_vendor": "H",
                "source_format": "parquet",
                "severity": "WARN" if bool(last_row["is_anomaly"]) else "INFO",
                "event_class": "sensor_snapshot",
            },
            "normalized_metrics": {
                "temperature_c": float(last_row["backside_temp_c"]),
                "gas_flow_sccm": int(last_row["flow_rate_sccm"]),
                "helium_pressure_psi": float(last_row["helium_pressure_psi"]),
            },
            "health_status": {
                "is_alarm": bool(last_row["is_anomaly"]),
                "alarm_code": "PRESSURE_SPIKE" if last_row["is_anomaly"] else "NONE",
                "health_score": 82 if last_row["is_anomaly"] else 98,
            },
            "vendor_metrics": {
                "sensor_id": last_row["sensor_id"],
                "anomaly_count": anomaly_count,
            },
        },
    )
    return record, 0.92, None


def find_existing_files(patterns):
    seen = set()
    results = []
    for pattern in patterns:
        for file_path in sorted(glob.glob(pattern)):
            if os.path.isfile(file_path) and file_path not in seen:
                seen.add(file_path)
                results.append(file_path)
    return results


def translate_with_fallback(raw_text, deterministic_record, base_score, rules):
    confidence = evaluate_confidence(deterministic_record, base_score)
    if confidence >= CONFIDENCE_THRESHOLD:
        return deterministic_record, confidence, False
    ai_record, learned_mappings = get_ai_standardization(raw_text, deterministic_record, rules)
    if ai_record:
        if maybe_learn_from_ai(rules, learned_mappings):
            save_rules(rules)
        ai_confidence = evaluate_confidence(ai_record, 0.75)
        return deep_merge(base_record(), ai_record), ai_confidence, True
    return deterministic_record, confidence, False


def process_file(file_path, parser, rules, raw_mode=False):
    if raw_mode:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
        deterministic_record, base_score, _ = parser(raw_text, rules)
    else:
        deterministic_record, base_score, _ = parser(file_path)
        if file_path.endswith(".bin"):
            with open(file_path, "rb") as f:
                raw_text = f.read().hex()
        elif file_path.endswith(".parquet"):
            raw_text = f"Parquet source file: {os.path.basename(file_path)}"
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()

    final_record, confidence, used_ai = translate_with_fallback(raw_text, deterministic_record, base_score, rules)
    final_record["translation_metadata"] = {
        "source_file": os.path.basename(file_path),
        "deterministic_confidence": round(evaluate_confidence(deterministic_record, base_score), 3),
        "final_confidence": round(confidence, 3),
        "used_ai_fallback": used_ai,
    }
    return final_record


def standardize():
    output_data = []
    rules = load_rules()
    structured_specs = [
        (["input.json", "vendor_a*.json"], parse_vendor_a_json),
        (["input.xml", "vendor_b*.xml"], parse_vendor_b_xml),
        (["input.csv", "vendor_c*.csv"], parse_vendor_c_csv),
        (["input.bin", "vendor_e*.bin"], parse_vendor_e_binary),
        (["input.parquet", "vendor_h*.parquet", "*sensor_dump.parquet"], parse_vendor_h_parquet),
    ]
    text_specs = [
        (["input.log", "vendor_d*.log"], parse_vendor_d_text),
        (["input_syslog.log", "vendor_f*.log"], parse_vendor_f_syslog),
        (["input.txt", "vendor_g*.txt"], parse_vendor_g_key_value),
    ]
    for patterns, parser in structured_specs:
        for file_path in find_existing_files(patterns):
            output_data.append(process_file(file_path, parser, rules, raw_mode=False))
    for patterns, parser in text_specs:
        for file_path in find_existing_files(patterns):
            output_data.append(process_file(file_path, parser, rules, raw_mode=True))
    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4)
    print(f"Processed {len(output_data)} sources. Results saved to output.json")


def to_float(value):
    try:
        return None if value in (None, "") else float(value)
    except (ValueError, TypeError):
        return None


def to_int(value):
    try:
        return None if value in (None, "") else int(float(value))
    except (ValueError, TypeError):
        return None


def average(values):
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 3) if clean else None


def extract_last_float(raw_text, pattern):
    matches = re.findall(pattern, raw_text, flags=re.IGNORECASE)
    return to_float(matches[-1]) if matches else None


def last_nonempty_line(raw_text):
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return lines[-1] if lines else None


if __name__ == "__main__":
    standardize()
