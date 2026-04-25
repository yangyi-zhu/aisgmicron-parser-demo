import argparse
import json
import random
import time
from pathlib import Path

from combgen import AdvancedSemiLogGenerator


def emit_one(gen: AdvancedSemiLogGenerator, out_dir: Path, index: int) -> None:
    choices = ["json", "xml", "csv", "log", "bin", "syslog", "txt", "parquet"]
    kind = random.choice(choices)

    if kind == "json":
        path = out_dir / f"vendor_a_{index}.json"
        path.write_text(json.dumps(gen.generate_vendor_a_complex(), indent=2), encoding="utf-8")
        return

    if kind == "xml":
        path = out_dir / f"vendor_b_{index}.xml"
        data = gen.generate_vendor_b_xml()
        from xml.etree.ElementTree import Element, SubElement, tostring
        from xml.dom import minidom

        root = Element("ProcessData")
        for key, value in data.items():
            child = SubElement(root, key)
            child.text = str(value)
        xml_str = minidom.parseString(tostring(root)).toprettyxml(indent="    ")
        path.write_text(xml_str, encoding="utf-8")
        return

    if kind == "csv":
        path = out_dir / f"vendor_c_{index}.csv"
        rows = gen.generate_vendor_c_timeseries(num_rows=30)
        import csv

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return

    if kind == "log":
        path = out_dir / f"vendor_d_{index}.log"
        path.write_text(gen.generate_vendor_d_text(), encoding="utf-8")
        return

    if kind == "bin":
        path = out_dir / f"vendor_e_{index}.bin"
        path.write_bytes(gen.generate_vendor_e_binary())
        return

    if kind == "syslog":
        path = out_dir / f"vendor_f_{index}.log"
        path.write_text(gen.generate_syslog(), encoding="utf-8")
        return

    if kind == "parquet":
        path = out_dir / f"vendor_h_{index}.parquet"
        df = gen.generate_vendor_h_parquet(num_rows=1000)
        df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
        return

    path = out_dir / f"vendor_g_{index}.txt"
    path.write_text(gen.generate_key_value(), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="watched_logs")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gen = AdvancedSemiLogGenerator(output_dir=str(out_dir))

    for i in range(1, args.count + 1):
        emit_one(gen, out_dir, i)
        print(f"generated #{i}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
