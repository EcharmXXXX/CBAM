"""证书资格、可售权属上限与出清约束行（§10）。

- 签发上限：z[j,t] <= chi * rho * sum_{d not EU} A[j,d] * x[j,d,t]；
  A 按规则 exclude_eu_delivery 排除欧盟交付（式14）；
- 出清：S_t = sum_j v_j z_jt >= kappa * L_t（式15，硬约束）；
- 证书价格 p_t 为出清行的对偶值除以折现因子，不进目标函数（式16）。
"""
from dataclasses import dataclass


@dataclass
class CertificateSystem:
    mrv: dict                  # year -> eur per certificate
    eu_use_rule: str
    eligible: dict              # (recipe_id, year) -> (chi, rho, v)

    def attribute_use(self, recipe_id, destination, case):
        """A[j,d]：属性使用兼容系数；主版排除欧盟交付。"""
        if self.eu_use_rule == "exclude_eu_delivery" and destination == case.eu_market:
            return 0.0
        return 1.0


def build(case, recipes):
    eligible = {}
    mrv = {}
    eu_rule = ""
    for (rule_case, year), row in case.certificate_rules.items():
        mrv[year] = float(row["mrv_eur_per_certificate"])
        eu_rule = row["eu_use_rule"]
    for (rid, year), recipe in recipes.items():
        if recipe.chi and recipe.is_steel:
            rho = case.param("COMMON", "transferable_share", rid)
            if rho is None:
                raise ValueError(f"transferable_share missing for eligible recipe {rid}")
            eligible[(rid, year)] = (recipe.chi, float(rho), recipe.v)
    return CertificateSystem(mrv=mrv, eu_use_rule=eu_rule, eligible=eligible)
