import unittest
from greenbook.model.energy_supply import EnergyInput,solve_energy


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
