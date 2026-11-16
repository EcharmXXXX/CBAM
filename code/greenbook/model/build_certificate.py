"""证书资格、可售权属上限与出清约束行（§10）。

- 签发上限：z[j,t] <= chi * rho * sum_{d not EU} A[j,d] * x[j,d,t]；
  A 按规则 exclude_eu_delivery 排除欧盟交付（式14）；
- 出清：S_t = sum_j v_j z_jt >= kappa * L_t（式15，硬约束）；
- 证书价格 p_t 为出清行的对偶值除以折现因子，不进目标函数（式16）。
"""
from dataclasses import dataclass
import math


@dataclass
class CertificateSystem:
    mrv: dict                  # year -> eur per certificate
    eu_use_rule: dict           # year -> compatibility rule
    eligible: dict              # (recipe_id, year) -> (chi, rho, v)

    def attribute_use(self, recipe_id, destination, case, year):
        """A[j,d]：属性使用兼容系数；主版排除欧盟交付。"""
        if self.eu_use_rule[year] == "exclude_eu_delivery" and destination == case.eu_market:
            return 0.0
        return 1.0


def build(case, recipes):
    eligible = {}
    mrv = {}
    eu_rule = {}
    for (rule_case, year), row in case.certificate_rules.items():
        if rule_case != "MAIN":
            continue
        if row["eu_use_rule"] not in ("exclude_eu_delivery", "allow_all"):
            raise ValueError(f"Unknown EU attribute rule: {row['eu_use_rule']}")
        if str(row.get("banking_allowed", "0")) != "0" or str(row.get("cash_exit_allowed", "0")) != "0":
            raise ValueError("Banking and cash exit are not enabled in the annual main model")
        mrv[year] = float(row["mrv_eur_per_certificate"])
        if not math.isfinite(mrv[year]) or mrv[year] < 0:
            raise ValueError("MRV fees must be finite and nonnegative")
        eu_rule[year] = row["eu_use_rule"]
    for (rid, year), recipe in recipes.items():
        if recipe.chi and recipe.is_steel:
            rho = case.param("COMMON", "transferable_share", rid, year=year)
            if rho is None:
                raise ValueError(f"transferable_share missing for eligible recipe {rid}")
            if not 0 <= float(rho) <= 1:
                raise ValueError("transferable_share must lie between zero and one")
            if case.certificate_rules[("MAIN", year)].get("allowed_vintage", "any") != "any":
                raise ValueError("New-only certificates require production-to-investment cohort attribution")
            # Conservatively exclude an entire downstream recipe if its input
            # attributes have already generated a modelled precursor entry duty.
            precursor_entry = any(kind == "q" and key.split("|")[0] == rid
                for (crossing, t), terms in case.border_map.items() if t == year
                for kind, key in ((row["variable_kind"], row["variable_key"]) for row in terms))
            if precursor_entry:
                continue
            eligible[(rid, year)] = (recipe.chi, float(rho), recipe.v)
    return CertificateSystem(mrv=mrv, eu_use_rule=eu_rule, eligible=eligible)
