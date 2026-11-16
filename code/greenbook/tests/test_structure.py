"""§17.3 结构行为检查。

- 无需求时不得为发证而生产；无义务时不得发行证书；
- 共享能力池（EAF/DRI）跨配方共同占用，不得超过；
- HBI 只有实际入境欧盟才承担边境责任；内部交付不产生欧盟责任；
- PREC_HBI 标 included_elsewhere，物理排放只计一次；
- 出清互补松弛：p>0 时 S=κL；
- N/C/G1/G05 全部可解且需求平衡精确满足。
"""
import tempfile
import unittest
from pathlib import Path

from greenbook.make_instance import write_instance
from greenbook.data_model import Case
from greenbook.model.solve import solve_case

H = {"STEEL_X_BFBOF": 1.85, "STEEL_X_SCRAP": 0.375, "STEEL_X_H2DRI": 0.15, "HBI_X": 1.3125}


class StructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(__file__).resolve().parents[2] / "review_artifacts" / "structural_test_run"
        cls.tmp.mkdir(parents=True, exist_ok=True)
        cls.instance = write_instance(cls.tmp / "instance")
        cls.case = Case.load(cls.instance)
        cls.solutions = {s: solve_case(cls.case, s) for s in ("N", "C", "G1", "G05")}

    def test_all_scenarios_optimal_and_demand_met(self):
        for scenario, sol in self.solutions.items():
            self.assertEqual(sol.status, "optimal", f"{scenario}: {sol.status}")
            for (region, grade, demand_case, year), demand in self.case.demand.items():
                supplied = sum(v for (rid, d, t), v in sol.x.items()
                                if d == region and t == year)
                self.assertAlmostEqual(supplied, demand / 1e6, places=6,
                                       msg=f"{scenario} demand {region}/{year}")

    def test_no_demand_no_production_no_certs(self):
        case = Case.load(self.instance)
        case.demand = {k: 0.0 for k in case.demand}
        sol = solve_case(case, "G1")
        self.assertEqual(sol.status, "optimal")
        self.assertTrue(all(v < 1e-7 for v in sol.q.values()), "无需求时仍有生产")
        self.assertTrue(all(v < 1e-7 for v in sol.z.values()), "无需求时仍发行证书")

    def test_no_certificates_without_obligation(self):
        for scenario in ("N", "C"):
            self.assertTrue(all(v < 1e-9 for v in self.solutions[scenario].z.values()),
                            f"{scenario} 无义务却发行证书")

    def test_shared_capacity_respected(self):
        for scenario, sol in self.solutions.items():
            for (pool, t), (used, available) in sol.pool_usage.items():
                self.assertLessEqual(used, available + 1e-6,
                                     f"{scenario} {pool}/{t}: {used} > {available}")

    def test_shared_x_dri_pool_sums_all_activities(self):
        """X_DRI 由 R_X_H2DRI 与 R_X_HBIPROD 共同占用，不得各自用满。"""
        sol = self.solutions["G1"]
        used = (1.05 * sol.q[("R_X_H2DRI", 2027)] + 1.0 * sol.q[("R_X_HBIPROD", 2027)])
        _, available = sol.pool_usage[("X_DRI", 2027)]
        self.assertLessEqual(used, available + 1e-6)

    def test_hbi_border_charged_only_on_eu_entry(self):
        sol = self.solutions["C"]
        for t in self.case.years:
            q_hbieaf = sol.q[("R_EU_HBIEAF", t)]
            self.assertAlmostEqual(sol.y[("HBI_X", t)], 1.05 * q_hbieaf, places=6)
            # 钢材入境量 = X 配方输欧交付量；X 内部交付不产生欧盟责任
            steel_import = sum(sol.x[(rid, "EU", t)] for rid in
                                ("R_X_BFBOF", "R_X_SCRAP", "R_X_H2DRI"))
            y_steel = sum(sol.y[(cc, t)] for cc in H if cc.startswith("STEEL"))
            self.assertAlmostEqual(y_steel, steel_import, places=6)
            # L 与手算 h×y 一致（式10逐类截零后）
            manual = sum(H[cc] * sol.y[(cc, t)] for cc in H)
            self.assertAlmostEqual(sol.L[t], manual, places=6)

    def test_hbi_physical_emissions_counted_once(self):
        comps = self.case.emission_components[("R_EU_HBIEAF", 2027)]
        prec = [c for c in comps if c["component_id"] == "PREC_HBI"][0]
        self.assertEqual(prec["system_included"].strip(), "0")
        self.assertEqual(prec["included_elsewhere"].strip(), "1")
        hbi_prod = [c for c in self.case.emission_components[("R_X_HBIPROD", 2027)]][0]
        self.assertEqual(hbi_prod["system_included"].strip(), "1")

    def test_complementary_slackness(self):
        for scenario in ("G1", "G05"):
            sol = self.solutions[scenario]
            for t, price in sol.price.items():
                gap = sol.S[t] - sol.kappa * sol.L[t]
                if price > 1e-4:
                    self.assertLess(abs(gap), 1e-5,
                                    f"{scenario} {t}: p>0 但 S≠κL (gap={gap})")
                elif gap > 1e-5:
                    self.assertLess(price, 1e-4, f"{scenario} {t}: S>κL 但 p>0")

    def test_cumulative_emissions_and_delta_reported(self):
        cumulative = {s: sum(p["total"] for p in sol.emissions.values())
                      for s, sol in self.solutions.items()}
        for scenario, value in cumulative.items():
            self.assertGreater(value, 0.0)
        self.assertIn("C", cumulative)
        delta_g1 = cumulative["G1"] - cumulative["C"]
        self.assertIsInstance(delta_g1, float)


if __name__ == "__main__":
    unittest.main()
