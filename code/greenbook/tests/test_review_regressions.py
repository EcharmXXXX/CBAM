"""Regression checks for objective accounting and missing material inputs."""
from copy import deepcopy
from pathlib import Path
import unittest

from greenbook.data_model import Case
from greenbook.model.solve import solve_case

INSTANCE = Path(__file__).resolve().parents[1] / "instance"


class AccountingTests(unittest.TestCase):
    def test_missing_pellet_supply_fails_instead_of_creating_free_input(self):
        case = Case.load(INSTANCE)
        case.resources = {key: row for key, row in case.resources.items() if key[1] != "pellets"}
        with self.assertRaisesRegex(ValueError, "Missing resource supply.*pellets"):
            solve_case(case, "N")

    def test_cross_node_freight_in_objective_and_changes_solution(self):
        case = Case.load(INSTANCE)
        for (rid, _year), reqs in case.requirements.items():
            if rid == "R_EU_HBIEAF":
                for req in reqs:
                    if req["requirement_type"] == "opex":
                        req["coefficient"] = "-10000"
        low = solve_case(case, "N")
        changed = deepcopy(case)
        for key, row in changed.transport.items():
            if key[2] == "hbi":
                row["private_freight_eur_per_t"] = "1000000"
        high = solve_case(changed, "N")
        self.assertGreater(sum(v for (rid, _), v in low.q.items() if rid == "R_EU_HBIEAF"), 1.)
        self.assertLess(sum(v for (rid, _), v in high.q.items() if rid == "R_EU_HBIEAF"), 1e-7)
        for result in (low, high):
            total = sum(result.costs[k] for k in ("opex", "resource", "transport",
                "investment_net_of_residual", "origin_carbon_tax", "cbam_payment", "mrv"))
            self.assertAlmostEqual(result.objective_meur, total, places=5)


if __name__ == "__main__":
    unittest.main()
