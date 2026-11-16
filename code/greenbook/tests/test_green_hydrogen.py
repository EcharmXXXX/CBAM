from copy import deepcopy
from pathlib import Path
import unittest

from greenbook.data_model import Case
from greenbook.model.build_recipes import build
from greenbook.model.solve import solve_case


INSTANCE = Path(__file__).resolve().parents[1] / "instance"


class GreenHydrogenTests(unittest.TestCase):
    def setUp(self):
        self.case = Case.load(INSTANCE)

    def test_water_electrolysis_evidence_and_steel_threshold_are_required(self):
        case = self.case
        block = case.resources[("X", "h2", "H1", 2027)]
        key = ("R_X_H2DRI", 2027)
        original = build(case)[key]
        self.assertEqual(original.chi, 1)
        self.assertAlmostEqual(original.eA, 0.07 + 0.051 * 0.8)
        baseline_cbam = solve_case(case, "C")
        block["electricity_emissions_tco2e_per_th2"] = "20"
        polluting = build(case)[key]
        self.assertEqual(polluting.chi, 0)
        self.assertAlmostEqual(polluting.eA, 0.07 + 0.051 * 20)
        self.assertEqual(solve_case(case, "C").L, baseline_cbam.L)
        block["electricity_emissions_tco2e_per_th2"] = "0"
        for method, evidence in (("water_electrolysis", ""), ("steam_reforming", "evidence"),
                                  ("steam_reforming_ccs", "evidence")):
            block.update(hydrogen_production_method=method, renewable_power_evidence_id=evidence)
            self.assertEqual(build(case)[key].chi, 0)

    def test_green_hydrogen_caps_output_and_non_green_is_still_available(self):
        case = self.case
        year = 2027
        green_key = ("X", "h2", "H1", year)
        block = case.resources[green_key]
        block["upper_quantity"] = "10000"
        source = "R_X_H2DRI"
        variant = "R_X_GRID_H2DRI"
        case.recipes[(variant, year)] = dict(case.recipes[(source, year)], recipe_id=variant)
        case.emission_components[(variant, year)] = deepcopy(case.emission_components[(source, year)])
        case.requirements[(variant, year)] = deepcopy(case.requirements[(source, year)])
        for row in case.requirements[(variant, year)]:
            if row["input_id"] == "h2":
                row["input_id"] = "h2_grid"
        case.resources[("X", "h2_grid", "H1", year)] = dict(
            block, resource_id="h2_grid", upper_quantity="1500000",
            renewable_power_evidence_id="", private_price_per_unit="0")
        for t in case.years[1:]:
            for mapping in (case.recipes, case.requirements, case.emission_components):
                mapping[(variant, t)] = deepcopy(mapping[(source, t)])
            case.recipes[(variant, t)]["certificate_class"] = "NONE"
        case.steel_recipe_ids += (variant,)
        gsc = solve_case(case, "G1")
        self.assertEqual(gsc.status, "optimal")
        self.assertLessEqual(gsc.q[(source, year)], 10000 / 0.051 / 1e6 + 1e-9)
        self.assertLessEqual(gsc.z[(source, year)], 0.6 * 10000 / 0.051 / 1e6 + 1e-9)
        self.assertGreater(gsc.q[(variant, year)], 0)
        self.assertNotIn((variant, year), gsc.z)
        for name in ("N", "C"):
            self.assertGreater(solve_case(case, name).q[(variant, year)], 0)

    def test_downstream_hbi_traces_its_actual_producer_and_electricity(self):
        case = self.case
        key = ("R_EU_HBIEAF", 2027)
        case.recipes[key]["certificate_class"] = "GREEN_STEEL"
        recipes = build(case)
        self.assertEqual(recipes[key].chi, 1)
        self.assertAlmostEqual(recipes[key].eA, 0.08 + 0.0735 + 1.05 * 0.051 * 0.8)

        source = ("R_X_HBIPROD", 2027)
        variant = ("R_X_UNVERIFIED_HBI", 2027)
        case.recipes[variant] = dict(case.recipes[source], recipe_id=variant[0])
        case.emission_components[variant] = deepcopy(case.emission_components[source])
        case.requirements[variant] = deepcopy(case.requirements[source])
        for row in case.requirements[variant]:
            if row["input_id"] == "h2":
                row["input_id"] = "h2_unverified"
        case.resources[("X", "h2_unverified", "H1", 2027)] = dict(
            case.resources[("X", "h2", "H1", 2027)], renewable_power_evidence_id="")
        self.assertEqual(build(case)[key].chi, 0)
        for row in case.requirements[variant]:
            if row["requirement_type"] == "output":
                row["input_id"] = "hbi_unverified"
        self.assertEqual(build(case)[key].chi, 1)
