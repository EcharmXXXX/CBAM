from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from greenbook.analysis.audit import audit_solution
from greenbook.analysis.certificate_benchmarks import build_benchmarks, read_strict_benchmarks
from greenbook.data_model import Case
from greenbook.make_dynamic_instance import write_dynamic_instance
from greenbook.model.build_recipes import build as build_recipes
from greenbook.model.solve import Solution, solve_case


class BenchmarkPolicyTests(TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        path = write_dynamic_instance(Path(directory.name), start=2026, end=2030, revised_policy=True)
        cls.case = Case.load(path)
        cls.strict = read_strict_benchmarks(path / "certificate_strict_benchmarks.csv")
        cls.settings = dict(allow_hbi_trade=False, legacy_policy=False)
        cls.cbam = solve_case(cls.case, "C", **cls.settings)
        cls.schedule = build_benchmarks(cls.case, cls.cbam, cls.strict)

    def test_loose_benchmark_slices_deliveries_by_tonnage(self):
        year = 2026
        key = ("STEEL_AGG", year)
        bf = ("R_X_BFBOF", "EU", year)
        h2 = ("R_EU_H2DRI", "EU", year)
        reference = Solution("C", "optimal", 0.)
        reference.x = {k: v for k, v in self.cbam.x.items() if k[2] != year}
        reference.x.update({bf: 4., h2: 6., ("R_X_BFBOF", "X", year): 30.})
        recipes = build_recipes(self.case, qualify=False)
        high, low = (recipes[rid, year].eR for rid in (bf[0], h2[0]))
        row = build_benchmarks(self.case, reference, self.strict)[key]
        self.assertAlmostEqual(row["normal"], (4 * high + 6 * low) / 10)
        self.assertAlmostEqual(row["loose"], (4 * high + low) / 5)
        self.assertAlmostEqual(row["strict"], self.strict[key][0])
        self.assertEqual(row["strict_source"], self.strict[key][1])
        with self.assertRaisesRegex(ValueError, "Missing strict benchmark"):
            build_benchmarks(self.case, reference, {})

    def test_fixed_hydrogen_recipes_share_the_same_furnaces(self):
        recipes = build_recipes(self.case, qualify=False)
        local = [recipes[rid, 2026] for rid in ("R_EU_H2DRI", "R_EU_H2DRI_S125", "R_EU_H2DRI_S25")]
        self.assertEqual([recipe.scrap_share for recipe in local], [0., .125, .25])
        for recipe in local:
            self.assertEqual({pool for pool, _ in recipe.capacity_use}, {"EU_DRI", "EU_EAF"})
        self.assertEqual(recipes["R_EU_BFBOF", 2026].scrap_share, .125)
        self.assertEqual(recipes["R_EU_SCRAP", 2026].scrap_share, 1.)

    def test_retirement_uses_exporter_liability_and_common_grade_benchmark(self):
        year = 2026
        for tier in ("loose", "normal", "strict"):
            benchmarks = {key: row[tier] for key, row in self.schedule.items()}
            with self.subTest(tier=tier):
                sol = solve_case(self.case, f"G_{tier.upper()}", benchmarks=benchmarks, **self.settings)
                self.assertEqual(sol.status, "optimal")
                self.assertTrue(audit_solution(self.case, sol)["passed"])
                self.assertGreater(sum(sol.S.values()), 0)
                for t in self.case.years:
                    self.assertAlmostEqual(sol.S[t], sol.L[t])
                    payments = [p for (_, cy), p in sol.exporter_payments.items() if t == cy]
                    self.assertAlmostEqual(sum(p["certificate_purchase"] for p in payments), sol.price[t] * sol.S[t])
                self.assertTrue(all(exporter == "X" for exporter, _ in sol.exporter_payments))
                for (rid, t), row in sol.certificate_supply.items():
                    self.assertAlmostEqual(row["value_tco2e_per_tcs"],
                                           max(0, benchmarks["STEEL_AGG", t] - row["eR_tco2e_per_tcs"]))
                    self.assertGreaterEqual(row["threshold_tco2e_per_tcs"], row["eA_tco2e_per_tcs"])
                    self.assertLessEqual(row["issued_and_retired_MtCO2e"], row["proportional_ceiling_MtCO2e"] + 1e-8)
                with self.assertRaisesRegex(ValueError, "fixed one-for-one"):
                    solve_case(self.case, "G_NORMAL", benchmarks=benchmarks, kappa_override=0.5, **self.settings)
        self.assertGreater(self.cbam.exporter_payments["X", year]["cbam_payment"], 0)
        self.assertEqual(self.cbam.q["R_EU_HBIEAF", year], 0.)

    def test_resource_shipments_face_loss_and_source_constraints(self):
        sol = self.cbam
        self.assertTrue(audit_solution(self.case, sol)["passed"])
        positive = [(k, v) for k, v in sol.f.items() if v > 0]
        self.assertTrue(positive)
        for (origin, destination, resource, year), shipped in positive:
            self.assertLessEqual(shipped, sum(v for (node, res, _, y), v in sol.u.items()
                                               if (node, res, y) == (origin, resource, year)) + 1e-7)
            self.assertGreater(float(self.case.transport[origin, destination, resource, year]["loss_fraction"]), 0)
        altered = deepcopy(sol)
        key, value = positive[0]
        altered.f[key] = value + 1
        self.assertFalse(audit_solution(self.case, altered)["passed"])

    def test_absolute_rights_apply_only_to_eligible_steel(self):
        case = deepcopy(self.case)
        case.attribute_rights = [dict(recipe_id="R_X_H2DRI", year=2026, available_rights_tcs=0.)]
        self.assertEqual(solve_case(case, "C", **self.settings).status, "optimal")
        benchmarks = {key: row["normal"] for key, row in self.schedule.items()}
        sol = solve_case(case, "G_NORMAL", benchmarks=benchmarks, **self.settings)
        self.assertEqual(sol.status, "optimal")
        self.assertEqual(sol.z["R_X_H2DRI", 2026], 0.)
