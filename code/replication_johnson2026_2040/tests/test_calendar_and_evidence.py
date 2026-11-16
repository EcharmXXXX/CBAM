import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from replication_johnson2026_2040.calendar import ModelCalendar
from replication_johnson2026_2040.prepare_inputs import OUT, parse_gem, main as prepare_inputs_main
from replication_johnson2026_2040.check_readiness import check

BASE_CONFIG = dict(start_year=2026, end_year=2040, construction_years=3,
                   initial_state_date="2026-01-01",
                   modeled_routes=["BF-BOF", "SCRAP-EAF", "H2-DRI-EAF"],
                   pipeline_review_complete=False, snapshot_valid_for_initial_state=False,
                   inherit_2025_model_state=False, inherit_2022_2024_hold_policy=False)

STATE_FIELDS = ["region", "route", "capacity_tCS_year", "remaining_life_years", "utilization_limit",
               "as_of_date", "source_id", "value_status", "mapping_evidence"]


def state_row(region="China", route="BF-BOF", **overrides):
    row = dict(region=region, route=route, capacity_tCS_year="1000000", remaining_life_years="10",
               utilization_limit="0.9", as_of_date="2026-01-01", source_id="GEM_steel_2026_09",
               value_status="observed", mapping_evidence="facility level link")
    row.update(overrides)
    return row


def driver_row(year, region="China", scenario="baseline", **overrides):
    row = dict(year=year, region=region, demand_tCS="800000000", annual_ore_supply_t="1000000",
               annual_scrap_supply_t="500000", renewable_power_EUR_MWh="40", hydrogen_EUR_t="3000",
               origin_carbon_EUR_tCO2="80", source_product_basis="crude_steel",
               tCS_conversion_evidence="worldsteel finished-to-crude factor", source_id="WSIF_2026",
               value_status="projected", scenario_id=scenario)
    row.update(overrides)
    return row


def _write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_case(tmp, rows_by_file=None, config=None, calendar_rows=None):
    (tmp / "inputs").mkdir(parents=True, exist_ok=True)
    cfg = dict(BASE_CONFIG, **(config or {}))
    (tmp / "case_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    for filename, rows in (rows_by_file or {}).items():
        _write_csv(tmp / "inputs" / filename, rows)
    if calendar_rows is not None:
        _write_csv(tmp / "investment_calendar.csv", calendar_rows)


class CalendarAndEvidenceTests(unittest.TestCase):
    def test_final_investment_requires_2043_parameters(self):
        calendar = ModelCalendar()
        self.assertEqual(len(calendar.production_years), 15)
        self.assertEqual(calendar.commissioning_year(2026), 2029)
        self.assertEqual(calendar.commissioning_year(2040), 2043)
        self.assertEqual(calendar.parameter_years[-1], 2043)
        self.assertEqual(calendar.initial_pipeline_years, (2026, 2027, 2028))
        with self.assertRaises(ValueError):
            calendar.annual_index(2044)

    def test_source_units_and_duplicate_summaries_are_preserved(self):
        steel, world = parse_gem(OUT / "raw" / "gem_2026_operating_steel_capacity.csv")
        china = [row for row in steel if row["country"] == "China"]
        self.assertEqual(len(china), 1)
        self.assertEqual(china[0]["BOF"], 898833000.)
        self.assertAlmostEqual(sum(row["Total operating capacity"] for row in steel), world["Total operating capacity"])
        self.assertTrue(all(row["country"] not in ("World", "World %", "Outside China") for row in steel))

    def test_blank_inputs_are_not_usable_observed_initial_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp)
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertFalse(result["optimization_results_available"])
            self.assertTrue(any("33 missing pairs" in error for error in result["errors"]))
            self.assertTrue(any("file is missing" in error for error in result["errors"]))

    def test_single_region_cannot_satisfy_annual_coverage(self):
        rows = [driver_row(year) for year in range(2026, 2044)]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, {"annual_drivers.csv": rows})
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertTrue(any("missing region groups" in error for error in result["errors"]))

    def test_scenarios_with_complementary_year_gaps_still_fail(self):
        rows = [driver_row(year, scenario="baseline") for year in range(2026, 2044, 2)]
        rows += [driver_row(year, scenario="high_demand") for year in range(2027, 2044, 2)]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, {"annual_drivers.csv": rows})
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertTrue(any("missing years" in error for error in result["errors"]))

    def test_reviewed_pipeline_requires_project_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, config={"pipeline_review_complete": True})
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertTrue(any("pipeline marked reviewed but the project table is missing" in error
                                for error in result["errors"]))

    def test_string_false_flags_are_rejected(self):
        config = {"pipeline_review_complete": "false", "snapshot_valid_for_initial_state": "false"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, config=config)
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertTrue(any("must be a boolean" in error for error in result["errors"]))
            self.assertTrue(any("pipeline has not been reviewed" in error for error in result["errors"]))

    def test_invalid_values_and_duplicate_keys_fail(self):
        state = [
            state_row(route="BF-BOF", capacity_tCS_year="NaN"),
            state_row(route="SCRAP-EAF", utilization_limit="2"),
            state_row(route="SCRAP-EAF"),
        ]
        policy = [dict(year="2030", region="China", route="BF-BOF", ETS_price_EUR_tCO2="80",
                       free_allocation_fraction="5", benchmark_tCO2_tCS="1.5", ETS_correction="1",
                       source_id="EU_Carbon", value_status="projected", scenario_id="baseline")]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, {"initial_state.csv": state, "annual_policy.csv": policy})
            result = check(tmp)
            errors = " | ".join(result["errors"])
            self.assertFalse(result["input_ready"])
            self.assertIn("not finite", errors)
            self.assertIn("utilization_limit above 1", errors)
            self.assertIn("duplicate record", errors)
            self.assertIn("free_allocation_fraction above 1", errors)

    def test_short_rows_do_not_crash_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp)
            (tmp / "inputs" / "initial_state.csv").write_text(
                ",".join(STATE_FIELDS) + "\nChina,BF-BOF\n", encoding="utf-8-sig")
            result = check(tmp)
            self.assertFalse(result["input_ready"])
            self.assertTrue(any("missing" in error for error in result["errors"]))

    def test_calendar_and_check_stay_aligned_after_config_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(OUT / "raw", tmp / "raw")
            write_case(tmp, config={"start_year": 2027, "end_year": 2030, "construction_years": 2})
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_inputs_main(out_dir=tmp)
            with (tmp / "investment_calendar.csv").open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0], {"decision_year": "2027", "commissioning_year": "2029"})
            self.assertEqual(rows[-1], {"decision_year": "2030", "commissioning_year": "2032"})
            result = check(tmp)
            self.assertEqual(result["required_parameter_end_year"], 2032)
            self.assertFalse(any("investment_calendar" in error for error in result["errors"]))

    def test_stale_investment_calendar_is_reported(self):
        stale = [dict(decision_year=year, commissioning_year=year + 3) for year in range(2026, 2041)]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_case(tmp, config={"construction_years": 2}, calendar_rows=stale)
            result = check(tmp)
            self.assertTrue(any("does not match the calendar derived from case_config.json" in error
                                for error in result["errors"]))

    def test_parse_gem_rejects_nonfinite_negative_and_duplicate_rows(self):
        def gem_file(content):
            folder = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, folder, True)
            path = folder / "gem.csv"
            path.write_text(content, encoding="utf-8")
            return path

        with self.assertRaises(ValueError):
            parse_gem(gem_file("Country/area,Total operating capacity,BOF\nChina,nan,900\nWorld,1000,900\n"))
        with self.assertRaises(ValueError):
            parse_gem(gem_file("Country/area,Total operating capacity,BOF\nChina,-5,900\nWorld,995,900\n"))
        with self.assertRaises(ValueError):
            parse_gem(gem_file("Country/area,Total operating capacity,BOF\nChina,500,400\nChina,500,400\nWorld,1000,800\n"))


if __name__ == "__main__":
    unittest.main()
