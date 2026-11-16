import json
from pathlib import Path
import tempfile
import unittest

from greenbook.analysis.audit import audit_solution
from greenbook.data_model import Case
from greenbook.devlin_inputs import update_devlin_inputs
from greenbook.model.energy_supply import solve_energy
from greenbook.model.solve import solve_case
from greenbook.tests.test_energy_supply import fixture


def example(year, source):
    ore = dict(node_id="X", resource_id="pellets_dr_67", block_id="P1", year=year,
               pellet_fe_fraction=.67, pellet_gangue_fraction=.025, ore_limited_t_pellets=900000,
               pellet_capacity_t=750000, delivery_capacity_t=850000, other_use_t=50000,
               mined_ore_fe_fraction=.62, source_usd_to_model_eur=.9, freight_EUR_per_t=10.,
               emission_factor=.15, source_id=source)
    charge = dict(steel_fe_fraction=.99, eaf_dri_iron_recovery=.98, scrap_charge_share=0.)
    source_data = dict(year=year, emissions_source_id=source)
    reduction = dict(pellet_resource_id=ore["resource_id"], minimum_pellet_fe_fraction=.66,
                     maximum_pellet_gangue_fraction=.03, dri_fe_fraction=.92, iron_recovery=.97,
                     hydrogen_resource_id="h2", preheat_electric_efficiency=.9, dri_pool_id="X_DRI",
                     electricity_resource_id="power_x")
    steel = dict(source_data, **reduction, **charge, route="hot_dri", recipe_id="R_X_H2DRI",
                 nonresource_opex_EUR_per_t=45.)
    iron = dict(source_data, **reduction, route="hbi_production", recipe_id="R_X_HBIPROD",
                nonresource_opex_EUR_per_t=38., hbi_resource_id="hbi", briquette_yield=.99)
    import_steel = dict(
        source_data, **charge, route="hbi_eaf", recipe_id="R_EU_HBIEAF",
        nonresource_opex_EUR_per_t=65., hbi_resource_id="hbi", reheat_MWh_per_t=.15,
        electricity_resource_id="power_eu",
        logistics=dict(delivered_fraction=.95, border_fraction=.98, freight_EUR_per_t_shipped=25.,
                       handling_EUR_per_t_shipped=5., capacity_t_shipped=800000, emissions_tco2e_per_t_shipped=.03),
    )
    return dict(recipes=[steel, iron, import_steel], pellet_blocks=[ore])


class DevlinInputTests(unittest.TestCase):
    def test_pellet_hydrogen_eaf_and_three_hbi_quantities(self):
        case = Case.load(Path(__file__).resolve().parents[1] / "instance")
        year = 2027
        config = example(year, "TOY_STAGE_A")
        hot, _, hbi_eaf = config["recipes"]
        hot.update(scrap_charge_share=.25, scrap_fe_fraction=.99, eaf_scrap_iron_recovery=.98,
                   scrap_resource_id="scrap")
        for node, power in (("X", "power_x"), ("EU", "power_eu")):
            case.resources[(node, power, "1", year)] = dict(
                quantity_unit="MWh", upper_quantity=1e7, private_price_per_unit=40., emission_factor=.1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engineering.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            updated, report, _ = update_devlin_inputs(case, path)
            hbi_eaf["logistics"]["border_fraction"] = .9
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot receive"):
                update_devlin_inputs(case, path)
        self.assertNotIn(("X", "pellets_dr_67", "P1", year), case.resources)
        pellet = updated.resources[("X", "pellets_dr_67", "P1", year)]
        self.assertAlmostEqual(pellet["upper_quantity"], 700000.)
        self.assertAlmostEqual(pellet["private_price_per_unit"], 100.)
        requirements = {row["input_id"]: row["coefficient"]
                        for row in updated.requirements[("R_X_H2DRI", year)]}
        charge = .99 / ((.75 * .92 + .25 * .99) * .98)
        self.assertAlmostEqual(requirements["h2"], .04813 * charge * .75)
        self.assertAlmostEqual(requirements["scrap"], charge * .25)
        self.assertAlmostEqual(requirements["power_x"], .74 + charge * .75 * (.06 + 1606.1 / 3600. / .9))
        flow = next(row for row in report["recipes"] if row["route"] == "hbi_eaf")
        self.assertAlmostEqual(flow["hbi_shipped_t_per_tcs"] * .95, flow["hbi_charged_t_per_tcs"])
        self.assertAlmostEqual(updated.border_map[("HBI_X", year)][0]["coefficient"],
                               flow["hbi_shipped_t_per_tcs"] * .98)
        for scenario in ("N", "C", "G1", "G05"):
            solution = solve_case(updated, scenario)
            self.assertTrue(audit_solution(updated, solution)["passed"])
            self.assertLessEqual(solution.u[("X", "pellets_dr_67", "P1", year)], .7 + 1e-8)

    def test_shared_land_and_water_limits(self):
        data = fixture()
        data.wind_cf = [1., 0.]
        data.solar_cf = [0., 1.]
        data.capacity_limit.update(wind=1., solar=1.)
        data.renewable_land_km2 = 1. / 8. + 1. / 45. - .01
        self.assertEqual(solve_energy(data, power_mode="islanded")["status"], "infeasible")
        data.renewable_land_km2 += .01
        result = solve_energy(data, power_mode="islanded")
        self.assertEqual(result["status"], "optimal")
        self.assertAlmostEqual(result["annual_hydrogen_water_m3"], 20. * 8760 * .012)
        data.hydrogen_water_limit_m3_per_year = 100.
        self.assertEqual(solve_energy(data, power_mode="islanded")["status"], "infeasible")

    def test_hbi_and_finished_steel_can_share_one_shipping_capacity(self):
        case = Case.load(Path(__file__).resolve().parents[1] / "instance")
        year = 2027
        for commodity in ("steel", "hbi"):
            case.transport[("X", "EU", commodity, year)].update(
                annual_capacity_t=2e6, shared_corridor_id="X_EU_LIMIT")
        result = solve_case(case, "C")
        self.assertEqual(result.status, "optimal")
        hbi = 1.05 * result.q[("R_EU_HBIEAF", year)]
        steel = sum(q for (rid, dest, t), q in result.x.items()
                    if rid.startswith("R_X_") and dest == "EU" and t == year)
        self.assertLessEqual(hbi + steel, 2. + 1e-8)
