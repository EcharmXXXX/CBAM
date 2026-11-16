from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from greenbook.data_model import Case
from greenbook.energy_inputs import update_energy_supply
from greenbook.model.build_recipes import build
from greenbook.model.energy_supply import EnergyInput, solve_energy
from greenbook.model.solve import solve_case


def fixture():
    names=("wind","solar","electrolyser","battery_power","battery_energy","hydrogen_store")
    return EnergyInput([0.,0.],[0.,0.],[1.,1.],[20.,20.],[50.,50.],[100.,100.],[2.,2.],
        {name:0. for name in names},{name:0. if name not in ("electrolyser",) else 2. for name in names})


class EnergySupplyTests(unittest.TestCase):
    def test_grid_only_units_have_hand_computed_cost_and_emissions(self):
        result=solve_energy(fixture())
        self.assertEqual(result["status"],"optimal")
        self.assertAlmostEqual(result["private_cost_EUR_per_kg"],2.5)
        self.assertAlmostEqual(result["operational_grid_emissions_kgCO2_per_kgH2"],5.)
        self.assertLess(result["max_equality_residual"],1e-7)

    def test_no_energy_cannot_deliver_from_free_initial_storage(self):
        data=fixture();data.grid_limit_MW=[0.,0.];data.capacity_limit["hydrogen_store"]=100000.
        self.assertEqual(solve_energy(data)["status"],"infeasible")

    def test_hydrogen_store_shifts_actual_production_across_time(self):
        data=fixture();data.wind_cf=[1.,0.];data.grid_limit_MW=[0.,0.]
        data.capacity_limit["wind"]=2.;data.capacity_limit["hydrogen_store"]=20.
        result=solve_energy(data)
        self.assertEqual(result["status"],"optimal")
        self.assertAlmostEqual(result["operation"]["electrolysis"][0],2.)
        self.assertAlmostEqual(result["operation"]["electrolysis"][1],0.)
        self.assertAlmostEqual(result["capacities"]["hydrogen_store"],20.)

    def test_zero_delivery_is_rejected(self):
        data=fixture();data.delivery_kg_per_hour=[0.,0.]
        with self.assertRaises(ValueError):solve_energy(data)

    def test_islanded_price_uses_consumed_mwh_and_shared_cost_is_allocated_once(self):
        data = fixture()
        data.wind_cf = [1., 0.5]
        data.capacity_limit["wind"] = 4.
        data.direct_load_MW = [1., 1.]
        data.annual_cost.update(wind=87600., electrolyser=17520.)
        data.wind_emissions_kgCO2_MWh = 20.
        data.grid_price_EUR_MWh = [0., 0.]
        result = solve_energy(data, power_mode="islanded")
        self.assertEqual(result["status"], "optimal")
        self.assertAlmostEqual(result["capacities"]["wind"], 4.)
        self.assertAlmostEqual(result["annual_grid_purchase_MWh"], 0.)
        self.assertAlmostEqual(result["electricity_cost_EUR_MWh"], 20.)
        self.assertAlmostEqual(result["electricity_emissions_kgCO2e_MWh"], 30.)
        self.assertAlmostEqual(result["private_cost_EUR_per_kg"], 1.1)
        self.assertAlmostEqual(result["hydrogen_electricity_emissions_kgCO2e_per_kgH2"], 1.5)
        self.assertAlmostEqual(
            result["private_cost_EUR_per_kg"] * result["annual_delivered_kg"]
            + result["electricity_cost_EUR_MWh"] * result["annual_direct_electricity_MWh"],
            result["annual_private_cost_EUR"],
        )

    def test_grid_uses_external_industrial_tariff_and_fixed_load(self):
        data = fixture()
        data.wind_cf = [1., 1.]
        data.capacity_limit.update(wind=100., hydrogen_store=100.)
        data.annual_cost["electrolyser"] = 17520.
        data.grid_price_EUR_MWh = [40., 60.]
        data.direct_load_MW = [1., 1.]
        result = solve_energy(data, power_mode="grid")
        self.assertEqual(result["status"], "optimal")
        self.assertAlmostEqual(result["capacities"]["wind"], 0.)
        self.assertAlmostEqual(result["capacities"]["hydrogen_store"], 0.)
        self.assertEqual(result["operation"]["grid"], [2., 2.])
        self.assertAlmostEqual(result["electricity_cost_EUR_MWh"], 50.)
        self.assertAlmostEqual(result["private_cost_EUR_per_kg"], 2.6)
        self.assertAlmostEqual(result["hydrogen_electricity_emissions_kgCO2e_per_kgH2"], 5.)
        data.delivery_kg_per_hour = [10., 30.]
        with self.assertRaisesRegex(ValueError, "constant"):
            solve_energy(data, power_mode="grid")

    def test_engineering_supply_enters_all_scenarios_and_traces_only_verified_hydrogen(self):
        case = Case.load(Path(__file__).resolve().parents[1] / "instance")
        for year in case.years:
            initial = ("R_X_H2DRI", year)
            grid = ("R_X_GRID_H2DRI", year)
            case.recipes[grid] = dict(case.recipes[initial], recipe_id=grid[0])
            case.emission_components[grid] = deepcopy(case.emission_components[initial])
            case.requirements[grid] = deepcopy(case.requirements[initial])
            for row in case.requirements[grid]:
                if row["input_id"] == "h2":
                    row["input_id"] = "h2_grid"
            case.requirements[initial].append(dict(requirement_type="input", input_id="power_wind",
                                                    input_node="X", destination_node="X", coefficient=0.5))
        case.steel_recipe_ids += ("R_X_GRID_H2DRI",)
        projects = []
        for year in case.years:
            projects.extend([
                dict(node_id="X", resource_id="h2", block_id="H1", year=year, source_id="test",
                     power_mode="islanded", electricity_resource_id="power_wind",
                     renewable_power_evidence_id="test-evidence",
                     input=dict(hours=[1., 1.], solar_cf=0., wind_cf=1., delivery_kg_per_hour=200.,
                                direct_load_MW=1., annual_cost=fixture().annual_cost,
                                capacity_limit=dict(fixture().capacity_limit, wind=11., electrolyser=10.),
                                wind_emissions_kgCO2_MWh=20.)),
                dict(node_id="X", resource_id="h2_grid", block_id="H1", year=year, source_id="test",
                     power_mode="grid",
                     input=dict(hours=[1., 1.], delivery_kg_per_hour=20000.,
                                grid_limit_MW=1000., grid_price_EUR_MWh=10., grid_emissions_kgCO2_MWh=100.,
                                annual_cost={"electrolyser": 0.}, capacity_limit={"electrolyser": 1000.})),
            ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "energy.json"
            path.write_text(json.dumps(projects), encoding="utf-8")
            update_energy_supply(case, path)
            projects[1]["renewable_power_evidence_id"] = "wrong"
            path.write_text(json.dumps(projects), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot supply renewable"):
                update_energy_supply(case, path)
        self.assertEqual(case.resources[("X", "h2", "H1", 2027)]["upper_quantity"], 1752.)
        self.assertEqual(case.resources[("X", "h2_grid", "H1", 2027)]["private_price_per_unit"], 500.)
        self.assertEqual(case.resources[("X", "power_wind", "H1", 2027)]["emission_factor"], 0.02)
        recipes = build(case)
        self.assertEqual(recipes[("R_X_H2DRI", 2027)].chi, 1)
        self.assertEqual(recipes[("R_X_GRID_H2DRI", 2027)].chi, 0)
        for scenario in ("N", "C", "G1"):
            result = solve_case(case, scenario)
            self.assertEqual(result.status, "optimal")
            self.assertLessEqual(result.q[("R_X_H2DRI", 2027)], 1752. / 0.051 / 1e6 + 1e-8)
            self.assertGreater(result.q[("R_X_GRID_H2DRI", 2027)], 0.)
