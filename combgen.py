import json
import csv
import struct
import xml.etree.ElementTree as ET
from xml.dom import minidom
import random
from datetime import datetime, timedelta
import os
import pandas as pd

class AdvancedSemiLogGenerator:
    def __init__(self, output_dir="micron_advanced_logs"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.lot_ids = [f"LOT-{random.randint(10000,99999)}" for _ in range(5)]
        self.recipes = ["STI_ETCH_V3", "POLY_GATE_ETCH", "OXIDE_DEP_HighTemp"]
        self.operators = ["OP_102", "OP_205", "OP_SYSTEM"]
        self.chambers = ["CHAMBER-A1", "CHAMBER-A2", "CHAMBER-B1"]
        self.tools = ["PVD-CHAMBER-01", "METROLOGY-SCAN-05", "DIFFUSION-FURNACE-02"]
        
    def _generate_timestamp(self, seconds_offset=0):
        return datetime.now() - timedelta(seconds=seconds_offset)

    # ==========================================
    # Vendor A: JSON
    # ==========================================
    def generate_vendor_a_complex(self):
        is_anomaly = random.random() > 0.95
        return {
            "EventHeader": {
                "Timestamp": self._generate_timestamp().isoformat(),
                "EventID": f"EVT-{random.randint(1000000, 9999999)}",
                "ToolID": "ETCH-CHAMBER-04",
                "SoftwareVersion": "v12.4.5"
            },
            "LotContext": {
                "LotID": random.choice(self.lot_ids),
                "WaferID": f"W-{random.randint(1,25):02d}",
                "SlotID": random.randint(1, 25),
                "RecipeName": random.choice(self.recipes),
                "ProcessStep": random.randint(1, 6),
                "StepTimeSec": round(random.uniform(10.0, 120.0), 1)
            },
            "Measurements": {
                "RF_System": {
                    "SourcePower_W": random.randint(1000, 1500),
                    "SourceReflected_W": random.randint(0, 15) if not is_anomaly else random.randint(100, 300),
                    "BiasPower_W": random.randint(200, 500),
                    "BiasReflected_W": random.randint(0, 10),
                    "MatchNetwork": {"Capacitor1_Pos": random.randint(40, 60), "Capacitor2_Pos": random.randint(20, 80)}
                },
                "GasPanel_MFC_sccm": {
                    "Ar": random.choice([0, 100, 200, 500]),
                    "O2": random.choice([0, 10, 50]),
                    "CF4": random.choice([0, 30, 80]),
                    "CHF3": random.choice([0, 40, 90]),
                    "Cl2": random.choice([0, 20, 50])
                },
                "Temperature_C": {
                    "ESC_Temp": round(random.uniform(60.0, 60.5) if not is_anomaly else random.uniform(65.0, 75.0), 2),
                    "Wall_Temp": round(random.uniform(79.5, 80.5), 2),
                    "Showerhead_Temp": round(random.uniform(89.0, 91.0), 2)
                },
                "VacuumSystem": {
                    "ChamberPressure_mTorr": round(random.uniform(10.0, 15.0), 2),
                    "ThrottleValve_Angle": round(random.uniform(15.0, 45.0), 1),
                    "TurboPump_Speed_RPM": random.randint(29000, 30000)
                }
            },
            "HardwareAlarms": {
                "ActiveAlarms": ["ESC_TEMP_DEVIATION"] if is_anomaly else [],
                "InterlockStatus": "OK" if not is_anomaly else "WARNING"
            }
        }

    # ==========================================
    # Vendor B: XML
    # ==========================================
    def generate_vendor_b_xml(self):
        is_anomaly = random.random() > 0.9
        
        data = {
            "LOG_TIME": self._generate_timestamp().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "EQUIPMENT_NAME": "CVD_SYS_01",
            "RECIPE": "DEP_OX_THICK",
            "STEP": random.randint(1, 10),
            "LOT_ID": random.choice(self.lot_ids),
            "HEATER_ZONE_1_C": round(random.uniform(399, 401), 1),
            "HEATER_ZONE_2_C": round(random.uniform(399, 401), 1),
            "HEATER_ZONE_3_C": round(random.uniform(399, 401), 1),
            "HEATER_ZONE_4_C": round(random.uniform(399, 401), 1) if not is_anomaly else round(random.uniform(380, 390), 1),
            "CHAMBER_PRESS_TORR": round(random.uniform(2.5, 2.6), 3),
            "FORELINE_PRESS_TORR": round(random.uniform(0.1, 0.2), 3),
            "GAS_SIH4_SCCM": random.randint(195, 205),
            "GAS_N2O_SCCM": random.randint(990, 1010),
            "GAS_N2_SCCM": random.randint(4900, 5100),
            "RF_HIGH_FREQ_W": random.randint(890, 910),
            "RF_LOW_FREQ_W": random.randint(290, 310),
            "SPINDLE_SPEED_RPM": random.randint(0, 0), 
            "ALARM_ID": "ERR-774-HEATER" if is_anomaly else "0",
            "ALARM_TEXT": "ZONE 4 TEMP LOW" if is_anomaly else "NONE"
        }
        return data

    # ==========================================
    # Vendor C: CSV (Time-Series)
    # ==========================================
    def generate_vendor_c_timeseries(self, num_rows=100):
        rows = []
        base_time = self._generate_timestamp(seconds_offset=num_rows)
        current_step = 1
        
        for i in range(num_rows):
            if i > 0 and i % 20 == 0:
                current_step += 1
            drift = (i * 0.05) if current_step == 3 else 0 
            
            row = {
                "Timestamp": (base_time + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S"),
                "Tool": "LITHO-SCANNER-02",
                "Lot": "LOT-A882",
                "Wafer": "W-12",
                "StepID": current_step,
                "Laser_Power_mW": round(random.uniform(10.0, 10.1) - drift, 3),
                "Dose_mJ_cm2": round(random.uniform(29.8, 30.2), 2),
                "Focus_Offset_nm": random.randint(-15, 15),
                "Leveling_Z_um": round(random.uniform(-0.5, 0.5), 3),
                "Stage_X_Pos_mm": round(random.uniform(0, 300), 2),
                "Stage_Y_Pos_mm": round(random.uniform(0, 300), 2),
                "Lens_Temp_C": round(random.uniform(22.00, 22.05) + drift, 3),
                "Scan_Speed_mm_s": round(random.uniform(250.0, 310.0), 2),
                "Reticle_ID": "MASK-7742-REV-B",
                "Alignment_Error_X_nm": random.randint(-5, 5),
                "Alignment_Error_Y_nm": random.randint(-5, 5)
            }
            rows.append(row)
        return rows

    # ==========================================
    # Vendor D: Plain Text (.log)
    # ==========================================
    def generate_vendor_d_text(self):
        now = self._generate_timestamp()
        
        machine = "MCH0001"
        release = "VER0001"
        modules = ["DWIO", "ERLO"]
        components = ["DWDMxOA_rq.c", "ERLOlogfile.cpp"]
        
        def fmt_ts(ts):
            return ts.strftime("%d/%m/%Y  %H:%M:%S.") + f"{int(ts.microsecond/1000):03d}"
        
        lines = []

        # --- Event 1: Default OFF ---
        ts1 = now
        lines.append(f"{fmt_ts(ts1)}  Machine:{machine}  (Rel:{release}, {random.choice(modules)} [dc], {random.choice(components)}, ?,?, {random.randint(200,500)})")
        lines.append("SYSTEM EVENT: ER-OFF DEFAULT (linked to DW-20E2)")
        lines.append("DEACTIVATE: DW-20e2")
        lines.append("DEACTIVATE: DW-20e2")
        lines.append("")

        # --- Event 2: Warning block ---
        ts2 = ts1 + timedelta(milliseconds=100 + random.randint(0, 200))
        current_scan = 15.00
        required_scan = 25.00

        lines.append(f"{fmt_ts(ts2)}  Machine:{machine}  (Rel:{release}, DWIO [dc], DWDMxOA_rq.c, ?,?, {random.randint(200,500)})")
        lines.append("SYSTEM WARNING: DW-20E2 DEFAULT")
        lines.append(f"The scan time is too short to perform an OASIS light measurement. Current scan time: {current_scan:.2f} [ms], required minimum scan time: {required_scan:.2f} [ms]")
        lines.append("Scan time too short to program oasis light trigger.")
        lines.append(f"Time specified: {current_scan:.2f} ms")
        lines.append(f"Minimal time required: {required_scan:.2f} ms")
        lines.append("[DWDMxOA_rq_program_light_trigger:DWFMxOA_LIGHT_TIMING_WARNING]")
        lines.append("")

        # --- Event 3: Repeat OFF ---
        ts3 = ts2 + timedelta(milliseconds=100 + random.randint(0, 200))
        lines.append(f"{fmt_ts(ts3)}  Machine:{machine}  (Rel:{release}, DWIO [dc], DWDMxOA_rq.c, ?,?, {random.randint(200,500)})")
        lines.append("SYSTEM EVENT: ER-OFF DEFAULT (linked to DW-20E2)")
        lines.append("DEACTIVATE: DW-20e2")
        lines.append("DEACTIVATE: DW-20e2")
        lines.append("")

        # --- Event 4: Another warning (optional variation) ---
        if random.random() > 0.3:
            ts4 = ts3 + timedelta(milliseconds=100 + random.randint(0, 200))
            lines.append(f"{fmt_ts(ts4)}  Machine:{machine}  (Rel:{release}, DWIO [dc], DWDMxOA_rq.c, ?,?, {random.randint(200,500)})")
            lines.append("SYSTEM WARNING: DW-20E2 DEFAULT")
            lines.append(f"The scan time is too short to perform an OASIS light measurement. Current scan time: {current_scan:.2f} [ms], required minimum scan time: {required_scan:.2f} [ms]")
            lines.append("Scan time too short to program oasis light trigger.")
            lines.append(f"Time specified: {current_scan:.2f} ms")
            lines.append(f"Minimal time required: {required_scan:.2f} ms")
            lines.append("[DWDMxOA_rq_program_light_trigger:DWFMxOA_LIGHT_TIMING_WARNING]")
            lines.append("")

        return "\n".join(lines)

    # ==========================================
    # Vendor E: Binary (.bin)
    # ==========================================
    def generate_vendor_e_binary(self):
        is_anomaly = random.random() > 0.95
        
        step_id = random.randint(1, 100)
        recipe_hash = random.randint(100000, 999999)
        status_bits = 0b00000001 if not is_anomaly else 0b10000011 

        fwd_power = random.uniform(1195, 1205)
        ref_power = random.uniform(0, 5) if not is_anomaly else random.uniform(50, 100)
        bias_v = random.uniform(-450, -400)
        esc_voltage = random.randint(1800, 2200)   

        throttle_pos = random.uniform(20.0, 35.0)
        mfc_1_flow = random.uniform(99.0, 101.0)
        mfc_2_flow = random.uniform(49.0, 51.0)

        pump_temp = random.randint(40, 80)
        cycle_count = random.randint(50000, 100000)

        fmt = "i I B d d d H f f f B I"
        
        binary_data = struct.pack(fmt, 
            step_id, recipe_hash, status_bits,
            fwd_power, ref_power, bias_v, 
            esc_voltage, 
            throttle_pos, mfc_1_flow, mfc_2_flow,
            pump_temp, cycle_count
        )
        
        return binary_data
    
    # ==========================================
    # Vendor F: Syslog Format (RFC 3164 style)
    # ==========================================
    def generate_syslog(self, num_entries=20):
        levels = ["INFO", "WARN", "ERROR", "CRITICAL"]
        messages = [
            "Vacuum pump reached baseline pressure",
            "Wafer centering alignment completed",
            "Gate valve state changed to OPEN",
            "Cooling water flow rate fluctuation detected",
            "Heater power supply interlock triggered"
        ]
        
        log_lines = []
        for _ in range(num_entries):
            ts = self._generate_timestamp()
            host = random.choice(self.tools)
            level = random.choices(levels, weights=[70, 15, 10, 5])[0]
            msg = random.choice(messages)
            metric_segments = []
            if "Vacuum pump" in msg:
                metric_segments.extend([
                    f"pressure_torr={round(random.uniform(2.3, 2.7), 3)}",
                    f"foreline_torr={round(random.uniform(0.09, 0.18), 3)}",
                ])
            if "alignment" in msg:
                metric_segments.extend([
                    f"alignment_error_x_nm={random.randint(-4, 4)}",
                    f"alignment_error_y_nm={random.randint(-4, 4)}",
                ])
            if "Gate valve" in msg:
                metric_segments.append(f"valve_position_pct={random.randint(95, 100)}")
            if "Cooling water" in msg:
                metric_segments.extend([
                    f"flow_sccm={random.randint(430, 560)}",
                    f"temp_c={round(random.uniform(28.0, 33.0), 2)}",
                ])
            if "interlock" in msg:
                metric_segments.extend([
                    f"temp_c={round(random.uniform(380.0, 410.0), 1)}",
                    f"rf_forward_w={random.randint(850, 940)}",
                ])
            detail_suffix = f" | {' '.join(metric_segments)}" if metric_segments else ""
            # Format: <Priority>Timestamp Hostname Tag: Message
            line = f"<{random.randint(0, 191)}>{ts} {host} SEMI_APP: [{level}] {msg}{detail_suffix}"
            log_lines.append(line)
        
        return "\n".join(log_lines)

    # ==========================================
    # Vendor G: Key-Value Pair Format (.txt / .conf)
    # ==========================================
    def generate_key_value(self):
        tool = random.choice(self.tools)
        # Simulating a "State Dump" of a machine
        kv_pairs = {
            "system_id": tool,
            "status": "OPERATIONAL",
            "uptime_seconds": random.randint(1000, 50000),
            "last_recipe": "AL_DEP_V2",
            "chamber_temp_c": round(random.uniform(150, 450), 2),
            "foreline_pressure_torr": round(random.uniform(0.1, 0.3), 3),
            "gas_leak_test": "PASSED",
            "mfc_setpoint_sccm": 500,
            "mfc_actual_sccm": 499.8,
            "pump_temp_c": random.randint(35, 65),
            "error_count": 0 if random.random() > 0.1 else random.randint(1, 5)
        }
        
        # Format as key=value or key: value
        return "\n".join([f"{k}={v}" for k, v in kv_pairs.items()])
    
    # ==========================================
    # Vendor H: Parquet (High-Density Sensors)
    # ==========================================
    def generate_vendor_h_parquet(self, num_rows=1000):
        base_time = datetime.now()
        
        # Creating a dictionary of lists (columnar format)
        data = {
            "timestamp": [base_time + timedelta(milliseconds=i*100) for i in range(num_rows)],
            "sensor_id": ["SNS-HE-99"] * num_rows,
            "helium_pressure_psi": [round(random.uniform(20.0, 20.5), 4) for _ in range(num_rows)],
            "backside_temp_c": [round(random.uniform(30.0, 31.0), 2) for _ in range(num_rows)],
            "flow_rate_sccm": [random.randint(450, 550) for _ in range(num_rows)],
            "is_anomaly": [False] * num_rows
        }

        # Inject a burst of anomalies in the middle
        for i in range(400, 450):
            data["helium_pressure_psi"][i] += 5.0
            data["is_anomaly"][i] = True

        # Convert to DataFrame and save as Parquet
        df = pd.DataFrame(data)
        file_path = os.path.join(self.output_dir, "vendor_h_sensor_dump.parquet")
        
        return df

    def export_json(self, data, filename):
        path = os.path.join(self.output_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def export_xml(self, data, filename, root_tag="ProcessData"):
        path = os.path.join(self.output_dir, filename)
        def build_elem(parent, dict_data):
            for key, val in dict_data.items():
                child = ET.SubElement(parent, key)
                if isinstance(val, dict):
                    build_elem(child, val)
                else:
                    child.text = str(val)
        root = ET.Element(root_tag)
        build_elem(root, data)
        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="    ")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml_str)

    def export_csv(self, data_list, filename):
        if not data_list: return
        path = os.path.join(self.output_dir, filename)
        keys = data_list[0].keys()
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(data_list)

    def export_text(self, text_content, filename):
        path = os.path.join(self.output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text_content)

    def export_binary(self, binary_data, filename):
        path = os.path.join(self.output_dir, filename)
        with open(path, "wb") as f:
            f.write(binary_data)

    def save_file(self, content, filename):
        path = os.path.join(self.output_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        print(f"Generated: {filename}")

if __name__ == "__main__":
    gen = AdvancedSemiLogGenerator()
    print("Generating Log...")

    for i in range(2):
        gen.export_json(gen.generate_vendor_a_complex(), f"vendor_a_deep_nested_{i+1}.json")

    for i in range(2):
        gen.export_xml(gen.generate_vendor_b_xml(), f"vendor_b_massive_tags_{i+1}.xml")

    csv_data = gen.generate_vendor_c_timeseries(num_rows=500)
    gen.export_csv(csv_data, "vendor_c_high_freq_fdc.csv")

    for i in range(2):
        gen.export_text(gen.generate_vendor_d_text(), f"vendor_d{i+1}.log")

    for i in range(2):
        gen.export_binary(gen.generate_vendor_e_binary(), f"vendor_e{i+1}.bin")

    for i in range(2):
        syslog_content = gen.generate_syslog()
        gen.save_file(syslog_content, f"vendor_f_syslog_{i+1}.log")
    
    for i in range(2):
        kv_content = gen.generate_key_value()
        gen.save_file(kv_content, f"vendor_g_status_{i+1}.txt")

    gen.generate_vendor_h_parquet(num_rows=5000)

    print(f"Generation complete.")
