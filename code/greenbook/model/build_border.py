"""构造CBAM入境责任类别与变量映射（§9）。

对每个 crossing class 逐类截零：
    h = exposed * max(0, SEE - SEFA - SECPP/RP)
责任数量 y 由 18 表映射到 q/x 变量；一次入境只映射一次。
"""
from dataclasses import dataclass, field


@dataclass
class Crossing:
    crossing_class: str
    year: int
    h: float                     # tCO2e / t goods（逐类截零后）
    pay_price: float             # 实际付款价 eur/tCO2e
    exposed_share: float
    terms: list = field(default_factory=list)  # (kind, key, coefficient)
    replaced: bool = True

    def variable_terms(self):
        """把 variable_key 解析为 ('q', recipe_id) 或 ('x', recipe_id, destination)。"""
        parsed = []
        for kind, key, coef in self.terms:
            parts = key.split("|")
            if kind == "q":
                parsed.append(("q", parts[0], None, coef))
            elif kind == "x" and len(parts) == 2:
                parsed.append(("x", parts[0], parts[1], coef))
            elif kind == "f" and len(parts) == 3:
                parsed.append(("f", parts[0], tuple(parts[1:]), coef))
            else:
                raise ValueError(f"Unsupported border mapping: {kind}/{key}")
        return parsed


def build(case):
    crossings = {}
    for (crossing_class, year), row in case.border_rules.items():
        if row["policy_case"] != "CBAM_MAIN":
            continue
        see = float(row["see_tco2e_per_t_goods"])
        sefa = float(row["sefa_tco2e_per_t_goods"])
        secpp = float(row["secpp_eur_per_t_goods"])
        reference = float(row["reference_price_eur_per_tco2e"])
        exposed = float(row["exposed_share"])
        if reference <= 0 or not 0 <= exposed <= 1 or min(see, sefa, secpp) < 0:
            raise ValueError(f"Invalid border coefficient: {crossing_class}/{year}")
        h = exposed * max(0.0, see - sefa - secpp / reference)
        terms = [(r["variable_kind"], r["variable_key"], float(r["coefficient"]))
                 for r in case.border_map[(crossing_class, year)]]
        crossings[(crossing_class, year)] = Crossing(
            crossing_class=crossing_class, year=year, h=h,
            pay_price=float(row["payment_price_eur_per_tco2e"]),
            exposed_share=exposed, terms=terms,
            replaced=row.get("replaced_scope", "").lower() not in ("unreplaced", "outside", "none"))
    return crossings
