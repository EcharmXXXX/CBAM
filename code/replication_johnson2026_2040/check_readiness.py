"""Report missing calibration inputs without converting missing values to zero.

The gate validates structure (required columns), values (numeric, finite,
within range), business-key uniqueness and full calendar coverage per
scenario, region and route. Missing files are reported as missing, never
treated as empty tables. This is a preparation gate, not a complete physical
or policy validator.
"""
from pathlib import Path
import csv
import json
import math

from .calendar import ModelCalendar
from .prepare_inputs import COUNTRIES, CONTRACT_COLUMNS, OUT

ANNUAL_TABLES = ("annual_drivers.csv", "annual_policy.csv", "retirement_schedule.csv", "technical_parameters.csv")
YEAR_CHECK_TABLES = ANNUAL_TABLES + ("uncovered_supply.csv",)
GROUP_FIELDS = {
    "annual_drivers.csv": ("region",),
    "annual_policy.csv": ("region", "route"),
    "retirement_schedule.csv": ("region", "route"),
    "technical_parameters.csv": ("region", "route"),
}
UNIQUENESS_FIELDS = {
    "initial_state.csv": ("region", "route"),
    "initial_reserves.csv": ("region",),
    "committed_projects.csv": ("project_id",),
    "annual_drivers.csv": ("scenario_id", "region", "year"),
    "annual_policy.csv": ("scenario_id", "region", "route", "year"),
    "retirement_schedule.csv": ("scenario_id", "region", "route", "year"),
    "technical_parameters.csv": ("scenario_id", "region", "route", "year", "parameter"),
}
NUMERIC_RULES = {
    "initial_state.csv": {"capacity_tCS_year": (0.0, None), "remaining_life_years": (0.0, None), "utilization_limit": (0.0, 1.0)},
    "initial_reserves.csv": {"remaining_iron_ore_t": (0.0, None)},
    "committed_projects.csv": {"capacity_tCS_year": (0.0, None)},
    "annual_drivers.csv": {"demand_tCS": (0.0, None), "annual_ore_supply_t": (0.0, None), "annual_scrap_supply_t": (0.0, None)},
    "annual_policy.csv": {"free_allocation_fraction": (0.0, 1.0), "benchmark_tCO2_tCS": (0.0, None)},
    "retirement_schedule.csv": {"original_capacity_surviving_tCS_year": (0.0, None)},
    "technical_parameters.csv": {"value": (None, None)},
    "route_mapping.csv": {"allocation_share": (0.0, 1.0), "conversion_tCS_per_t_product": (0.0, None)},
    "uncovered_supply.csv": {"uncovered_steel_supply_tCS": (0.0, None), "uncovered_emissions_tCO2": (0.0, None)},
}
OPTIONAL_COLUMNS = {"notes"}


def _text(row, name):
    value = row.get(name)
    return value.strip() if isinstance(value, str) else ""


def read_rows(path):
    """Return (fieldnames, rows); (None, None) when the file is missing."""
    if not path.exists():
        return None, None
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return reader.fieldnames or [], list(reader)


def _number(filename, i, row, field, errors, minimum=None, maximum=None):
    raw = _text(row, field)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{filename}:{i}: {field} is not a number: {raw}")
        return None
    if not math.isfinite(value):
        errors.append(f"{filename}:{i}: {field} is not finite: {raw}")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{filename}:{i}: {field} below {minimum}: {raw}")
    if maximum is not None and value > maximum:
        errors.append(f"{filename}:{i}: {field} above {maximum}: {raw}")
    return value


def _bool_flag(config, key, errors):
    value = config.get(key, False)
    if not isinstance(value, bool):
        errors.append(f"case_config.json: {key} must be a boolean, got {value!r}")
        return False
    return value


def check(case_dir=OUT):
    case_dir = Path(case_dir)
    config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8"))
    try:
        cal = ModelCalendar(config["start_year"], config["end_year"], config["construction_years"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"case_config.json does not define a usable calendar: {error}") from error
    errors = []

    pipeline_reviewed = _bool_flag(config, "pipeline_review_complete", errors)
    snapshot_valid = _bool_flag(config, "snapshot_valid_for_initial_state", errors)

    routes = config.get("modeled_routes", [])
    if len(routes) != 3 or len(set(routes)) != 3:
        errors.append("The requested three-route scope is not configured")

    inputs = case_dir / "inputs"
    records = {}
    sizes = {}
    for filename, columns in CONTRACT_COLUMNS.items():
        fieldnames, rows = read_rows(inputs / filename)
        records[filename] = rows
        sizes[filename] = 0 if rows is None else len(rows)
        if rows is None:
            errors.append(f"{filename}: file is missing")
            continue
        if not rows and not (filename == "committed_projects.csv" and pipeline_reviewed):
            errors.append(f"{filename}: no calibrated data rows")
        missing_columns = [c for c in columns if c not in fieldnames]
        if missing_columns:
            errors.append(f"{filename}: missing columns {missing_columns}")
        required = [c for c in columns if c not in OPTIONAL_COLUMNS]
        for i, row in enumerate(rows, start=2):
            missing = [name for name in required if not _text(row, name)]
            if missing:
                errors.append(f"{filename}:{i}: missing {','.join(missing)}")
            for field, (minimum, maximum) in NUMERIC_RULES.get(filename, {}).items():
                _number(filename, i, row, field, errors, minimum, maximum)
            region = _text(row, "region")
            if "region" in columns and region and region not in COUNTRIES:
                errors.append(f"{filename}:{i}: unknown region {region}")
            route = _text(row, "route")
            if "route" in columns and route and routes and route not in routes:
                errors.append(f"{filename}:{i}: route {route} is not one of the modeled routes")
            if filename in YEAR_CHECK_TABLES:
                raw_year = _text(row, "year")
                if raw_year:
                    try:
                        year = int(raw_year)
                    except ValueError:
                        errors.append(f"{filename}:{i}: invalid year: {raw_year}")
                    else:
                        if year not in cal.parameter_years:
                            errors.append(f"{filename}:{i}: year outside calendar: {year}")
                        elif year > 2026 and _text(row, "value_status").startswith("observed"):
                            errors.append(f"{filename}:{i}: future value cannot be an observation as of 2026")
            if filename in ("initial_state.csv", "initial_reserves.csv"):
                as_of = _text(row, "as_of_date")
                if as_of and as_of != config["initial_state_date"]:
                    errors.append(f"{filename}:{i}: date does not equal initial state date")

    # Business-key uniqueness.
    for filename, fields in UNIQUENESS_FIELDS.items():
        seen = set()
        for i, row in enumerate(records.get(filename) or [], start=2):
            key = tuple(_text(row, f) for f in fields)
            if key in seen:
                errors.append(f"{filename}:{i}: duplicate record {key}")
            seen.add(key)

    # Initial-state coverage: all region-route pairs, no unexpected pairs.
    expected_pairs = {(region, route) for region in COUNTRIES for route in routes}
    obtained_pairs = {(_text(r, "region"), _text(r, "route")) for r in records.get("initial_state.csv") or []}
    missing_pairs = expected_pairs - obtained_pairs
    unexpected_pairs = obtained_pairs - expected_pairs
    if missing_pairs:
        errors.append(f"Initial region-route coverage incomplete: {len(missing_pairs)} missing pairs")
    if unexpected_pairs:
        errors.append(f"initial_state.csv: unexpected region-route pairs {sorted(unexpected_pairs)}")

    # Annual tables: full parameter-year coverage per scenario, region and route.
    for filename, group_fields in GROUP_FIELDS.items():
        rows = records.get(filename) or []
        if not rows:
            continue
        years_by_group = {}
        for row in rows:
            try:
                year = int(_text(row, "year"))
            except ValueError:
                continue
            group = (_text(row, "scenario_id"),) + tuple(_text(row, f) for f in group_fields)
            years_by_group.setdefault(group, set()).add(year)
        for group, years in sorted(years_by_group.items()):
            missing_years = sorted(set(cal.parameter_years) - years)
            if missing_years:
                errors.append(f"{filename}: {group} missing years {missing_years}")
        for scenario in sorted({_text(row, "scenario_id") for row in rows}):
            if group_fields == ("region",):
                expected_groups = {(scenario, region) for region in COUNTRIES}
            else:
                expected_groups = {(scenario, region, route) for region in COUNTRIES for route in routes}
            missing_groups = expected_groups - set(years_by_group)
            if missing_groups:
                label = "region" if group_fields == ("region",) else "region-route"
                examples = sorted(missing_groups)[:3]
                errors.append(f"{filename}: scenario {scenario}: {len(missing_groups)} missing {label} groups, e.g. {examples}")

    # Committed project pipeline: a reviewed pipeline must have a table, and
    # a reviewed empty table means verified zero projects.
    project_rows = records.get("committed_projects.csv")
    if pipeline_reviewed and project_rows is None:
        errors.append("committed_projects.csv: pipeline marked reviewed but the project table is missing")
    if project_rows:
        for i, row in enumerate(project_rows, start=2):
            try:
                decision = int(_text(row, "decision_year"))
                commissioning = int(_text(row, "commissioning_year"))
            except ValueError:
                continue
            if commissioning < cal.start_year:
                errors.append(f"committed_projects.csv:{i}: commissioning year {commissioning} precedes the simulation start; it belongs in the initial state")
            if commissioning > cal.parameter_years[-1]:
                errors.append(f"committed_projects.csv:{i}: commissioning year {commissioning} beyond the parameter horizon")
            if decision >= commissioning:
                errors.append(f"committed_projects.csv:{i}: decision year {decision} is not before commissioning year {commissioning}")
    if not pipeline_reviewed:
        errors.append("Initial committed-project pipeline has not been reviewed; missing projects cannot be assumed zero")
    if not snapshot_valid:
        errors.append("September 2026 snapshot has not been reconciled to the January 2026 initial state")

    # Allocation shares must sum to one per capacity pool.
    shares = {}
    for row in records.get("route_mapping.csv") or []:
        pool = _text(row, "asset_or_capacity_pool")
        raw = _text(row, "allocation_share")
        if not pool or not raw:
            continue
        try:
            shares[pool] = shares.get(pool, 0.0) + float(raw)
        except ValueError:
            continue
    for pool, total in sorted(shares.items()):
        if abs(total - 1.0) > 1e-6:
            errors.append(f"route_mapping.csv: allocation shares for {pool} sum to {total}, expected 1")

    # The investment calendar must be derived from the same configuration.
    fieldnames, calendar_rows = read_rows(case_dir / "investment_calendar.csv")
    if calendar_rows is None:
        errors.append("investment_calendar.csv: file is missing")
    else:
        expected_calendar = {year: cal.commissioning_year(year) for year in cal.production_years}
        obtained_calendar = {}
        for i, row in enumerate(calendar_rows, start=2):
            try:
                obtained_calendar[int(_text(row, "decision_year"))] = int(_text(row, "commissioning_year"))
            except ValueError:
                errors.append(f"investment_calendar.csv:{i}: invalid year")
        if obtained_calendar != expected_calendar:
            errors.append("investment_calendar.csv: does not match the calendar derived from case_config.json; regenerate it with prepare_inputs")

    return dict(input_ready=not errors, optimization_results_available=False,
                start_year=cal.start_year, end_year=cal.end_year, production_periods=len(cal.production_years),
                required_parameter_end_year=cal.parameter_years[-1], input_rows=sizes, errors=errors,
                warning="This is a preparation gate, not a complete physical/policy validator or an implemented optimisation engine")


if __name__ == "__main__":
    result = check()
    (OUT / "input_readiness.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["input_ready"] else 2)
