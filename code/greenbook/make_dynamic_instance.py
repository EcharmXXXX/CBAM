"""Synthetic 2026--2040 structural fixture, never an observed 2026 baseline."""
import csv
from pathlib import Path
from .make_instance import write_instance


def write_dynamic_instance(directory, start=2026, end=2040):
    if end < start:
        raise ValueError("Invalid fixture years")
    directory = Path(directory)
    write_instance(directory)
    for path in directory.glob("*.csv"):
        with path.open(encoding="utf-8-sig",newline="") as file:
            reader = csv.DictReader(file)
            fields, rows = reader.fieldnames, list(reader)
        if "year" in fields and rows and rows[0].get("year", "").strip():
            first = min(int(row["year"]) for row in rows)
            template = [row for row in rows if int(row["year"]) == first]
            rows = [dict(row,year=year) for year in range(start,end+1) for row in template]
        for row in rows:
            if "source_id" in row:
                row["source_id"] = "SYNTHETIC_DYNAMIC_STRUCTURE"
            if "notes" in row:
                row["notes"] = "Synthetic structural validation only; not calibrated 2026 data"
            if "lead_years" in row:
                row["lead_years"] = 3
            if "start_year" in row:
                row["start_year"] = start-5
                row["retirement_year"] = end+1
        with path.open("w",encoding="utf-8-sig",newline="") as file:
            writer = csv.DictWriter(file,fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (directory/"README.md").write_text(
        f"# Synthetic dynamic structural fixture\n\n{start}--{end}; two regions, three-year construction, annual investment decisions.\n"
        "All quantities and prices are synthetic. This is not a 2026 observed baseline or a real policy calibration.\n",encoding="utf-8")
    return directory
