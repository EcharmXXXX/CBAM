"""Parse downloaded primary-source data and create missing-value input contracts.

All capacity numbers remain in their published technology and product classes.
EAF is not automatically classified as scrap EAF. DRI tonnes are not added to
crude-steel tonnes. September 2026 snapshots are not labelled January 2026.
The investment calendar is generated from the calendar defined in
case_config.json, so the readiness gate and the calendar cannot diverge.
"""
from pathlib import Path
import csv
import json
import math

from .calendar import ModelCalendar

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "Johnson2026_2040_重建"
COUNTRIES = ["China", "EU27", "India", "Japan", "US", "Russia", "South Korea", "Turkey", "Brazil", "Australia", "ROW"]
EU27 = {"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland",
        "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
        "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"}
ALIASES = {"United States": "US", "Türkiye": "Turkey"}

# Single source of truth for the input contracts; check_readiness imports this
# so the written headers and the validated headers can never drift apart.
CONTRACT_COLUMNS = {
    "initial_state.csv": ["region", "route", "capacity_tCS_year", "remaining_life_years", "utilization_limit",
                           "as_of_date", "source_id", "value_status", "mapping_evidence"],
    "initial_reserves.csv": ["region", "remaining_iron_ore_t", "as_of_date", "source_id", "value_status", "notes"],
    "committed_projects.csv": ["project_id", "region", "route", "capacity_tCS_year", "decision_year", "commissioning_year",
                               "status", "as_of_date", "source_id", "value_status", "notes"],
    "annual_drivers.csv": ["year", "region", "demand_tCS", "annual_ore_supply_t", "annual_scrap_supply_t",
                           "renewable_power_EUR_MWh", "hydrogen_EUR_t", "origin_carbon_EUR_tCO2",
                           "source_product_basis", "tCS_conversion_evidence", "source_id", "value_status", "scenario_id"],
    "annual_policy.csv": ["year", "region", "route", "ETS_price_EUR_tCO2", "free_allocation_fraction",
                           "benchmark_tCO2_tCS", "ETS_correction", "source_id", "value_status", "scenario_id"],
    "retirement_schedule.csv": ["year", "region", "route", "original_capacity_surviving_tCS_year",
                                "source_id", "value_status", "scenario_id"],
    "technical_parameters.csv": ["year", "region", "route", "parameter", "value", "unit", "price_base_year",
                                 "source_id", "value_status", "scenario_id"],
    "route_mapping.csv": ["asset_or_capacity_pool", "source_product", "source_technology", "model_route",
                          "allocation_share", "conversion_tCS_per_t_product", "source_id", "evidence", "value_status"],
    "uncovered_supply.csv": ["year", "region", "uncovered_steel_supply_tCS", "uncovered_emissions_tCO2",
                             "demand_boundary_method", "source_id", "value_status", "scenario_id", "notes"],
}


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _gem_cell(country, column, cell):
    try:
        value = float(cell.replace(",", ""))
    except (TypeError, ValueError, IndexError) as error:
        raise ValueError(f"Unknown or missing numeric GEM cell for {country} in column {column}; do not replace it with zero") from error
    if not math.isfinite(value):
        raise ValueError(f"Non-finite GEM capacity for {country} in column {column}: {cell}")
    if value < 0:
        raise ValueError(f"Negative GEM capacity for {country} in column {column}: {cell}")
    return value


def parse_gem(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    header = next(i for i, row in enumerate(rows) if row and row[0] == "Country/area")
    columns = rows[header]
    data = []
    seen_countries = set()
    published_world = None
    for row in rows[header + 1:]:
        if not row or not row[0].strip():
            continue
        if row[0] == "World %":
            continue
        # Country section ends at the first World total; later China/Outside
        # China summary rows must never be counted for a second time.
        if row[0] == "World":
            published_world = {columns[i]: _gem_cell("World", columns[i], row[i]) * 1000 for i in range(1, len(columns))}
            break
        if row[0] in seen_countries:
            raise ValueError(f"Duplicate GEM country row: {row[0]}")
        seen_countries.add(row[0])
        values = {columns[i]: _gem_cell(row[0], columns[i], row[i]) * 1000 for i in range(1, len(columns))}
        region = "EU27" if row[0] in EU27 else ALIASES.get(row[0], row[0])
        if region not in COUNTRIES:
            region = "ROW"
        data.append(dict(country=row[0], region=region, **values))
    if published_world is None:
        raise ValueError("Missing published GEM world total")
    for name, total in published_world.items():
        obtained = sum(row[name] for row in data)
        # Published source tables may round individual capacities to 1 ktpa.
        if abs(obtained - total) > 1000.:
            raise ValueError(f"Country sum differs from the published world total: {name}, {obtained - total} t/yr")
    return data, published_world


def _calendar_from_config(out_dir):
    """Build the calendar from case_config.json so every artifact shares it."""
    config_path = out_dir / "case_config.json"
    if not config_path.exists():
        return ModelCalendar(), None
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        return ModelCalendar(existing["start_year"], existing["end_year"], existing["construction_years"]), existing
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"case_config.json exists but does not define a usable calendar: {error}") from error


def main(out_dir=OUT):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    calendar, existing = _calendar_from_config(out_dir)
    source_register = []
    summary = {}
    mapping_rows = []
    datasets = [("steel", "gem_2026_operating_steel_capacity.csv", "tCS/year",
                 "https://docs.google.com/spreadsheets/d/1JRFtPluYp2TDaTpBo8_7KAPivE4S9IXnNCQVWLuzod8/edit?gid=1663214592"),
                ("dri", "gem_2026_operating_dri_capacity.csv", "tDRI/year",
                 "https://docs.google.com/spreadsheets/d/1_vLfBVQTC4snzHJRpZ7YHT8TJ1cnhtGNJOEq9nQOpTg/edit?gid=1861950790")]
    for name, filename, unit, url in datasets:
        data, world = parse_gem(out_dir / "raw" / filename)
        rows = []
        for row in data:
            rows.append(dict(**row, source_id=f"GEM_{name}_2026_09", snapshot="2026-09", unit=unit,
                             value_status="observed_snapshot", coverage="GEM tracked facilities; not a full census"))
        write_csv(out_dir / f"observed_{name}_capacity_by_country.csv", rows, list(rows[0]))
        metrics = list(world)
        regional = []
        for region in COUNTRIES:
            selected = [row for row in data if row["region"] == region]
            regional.append(dict(region=region, **{key: sum(row[key] for row in selected) for key in metrics},
                                 source_id=f"GEM_{name}_2026_09", snapshot="2026-09", unit=unit,
                                 value_status="derived_from_observed_snapshot",
                                 coverage="sum of GEM tracked country records; absent facilities are not assumed zero"))
        write_csv(out_dir / f"observed_{name}_capacity_by_region.csv", regional, list(regional[0]))
        for row in regional:
            if name == "steel":
                pools = [("BOF", "BF-BOF", "candidate_only; validate BF/BOF link and initial-state date"),
                         ("EAF", "", "unallocated; feedstock and DRI linkage required"),
                         ("IF", "", "outside selected three routes"),
                         ("other/ unspecified", "", "outside selected three routes or unknown")]
            else:
                pools = [(key, "H2-DRI-EAF" if key == "hydrogen (green)" else "",
                          "iron capacity only; needs downstream EAF and conversion evidence" if key == "hydrogen (green)"
                          else "outside green-H2 route or unknown; do not add to crude-steel capacity")
                         for key in metrics if key != "Total operating capacity"]
            for pool, candidate, status in pools:
                mapping_rows.append(dict(region=row["region"], source_product="crude_steel" if name == "steel" else "DRI",
                    source_capacity_pool=pool, reported_capacity=row[pool], unit=unit, candidate_model_route=candidate,
                    allocation_status=status, snapshot="2026-09", source_id=f"GEM_{name}_2026_09"))
        summary[name] = dict(country_records=len(data), published_world=world,
                             region_totals={key: sum(row[key] for row in regional) for key in metrics})
        source_register.append(dict(source_id=f"GEM_{name}_2026_09", url=url, release="September 2026 V1",
            observation_period="2026 snapshot; annual March release with June/September limited updates",
            intended_use="capacity evidence; route mapping and January-1 backcast still required"))
    source_register.append(dict(source_id="WORLDSTEEL_FIGURES_2026",
        url="https://worldsteel.org/data/world-steel-in-figures/world-steel-in-figures-2026/", release="2026 edition",
        observation_period="annual production and demand mainly 2025; not observed full-year 2026",
        intended_use="historical calibration and consistency checks"))
    write_csv(out_dir / "source_register.csv", source_register, list(source_register[0]))
    write_csv(out_dir / "capacity_mapping_and_uncovered_pools.csv", mapping_rows, list(mapping_rows[0]))
    config = dict(start_year=calendar.start_year, end_year=calendar.end_year,
        production_periods=len(calendar.production_years), construction_years=calendar.construction_years,
        parameter_end_year=calendar.parameter_years[-1], initial_state_date="2026-01-01",
        initial_state_basis="observed_2026_reinitialization", use_2022_warmup=False, inherit_2025_model_state=False,
        inherit_2022_2024_hold_policy=False, route_scope="original_three_routes_with_explicit_uncovered_capacity",
        modeled_routes=["BF-BOF", "SCRAP-EAF", "H2-DRI-EAF"],
        boundary="three-route subsystem; excluded supply must be explicitly reconciled with total demand",
        observed_capacity_snapshot="2026-09", snapshot_valid_for_initial_state=False,
        pipeline_review_complete=False, status="data_preparation_only; not calibrated; no 2026--2040 solver results")
    if existing is not None:
        # Preserve manually collected configuration; this scope choice was
        # explicitly supplied by the user after the initial preparation.
        existing.update({key: config[key] for key in ("route_scope", "modeled_routes", "boundary", "status")})
        config = existing
    (out_dir / "case_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "source_capacity_checks.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # These contracts deliberately contain no fabricated numeric inputs. Existing
    # files are retained, so re-running evidence preparation cannot erase later
    # manual data collection.
    folder = out_dir / "inputs"
    folder.mkdir(exist_ok=True)
    for filename, columns in CONTRACT_COLUMNS.items():
        if not (folder / filename).exists():
            write_csv(folder / filename, [], columns)
    calendar_rows = [dict(decision_year=year, commissioning_year=calendar.commissioning_year(year))
                    for year in calendar.production_years]
    write_csv(out_dir / "investment_calendar.csv", calendar_rows, ["decision_year", "commissioning_year"])
    print(json.dumps(dict(status=config["status"], start_year=calendar.start_year, end_year=calendar.end_year,
                         production_years=calendar.production_years, parameter_years=calendar.parameter_years,
                         source_country_records={k: v["country_records"] for k, v in summary.items()}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
