"""由11/12/19/21表构造配方：投入、共享能力占用、三本账排放与证书资格。

结构要点（§17.3）：
- EAF等共享池由多个配方共同占用，占用系数之和受单一能力约束；
- HBI经R_X_HBIPROD中间活动供给，同一吨HBI只形成一次粗钢；
- 排放分量含 included_elsewhere 标志，物理系统账不重复计入前体；
- 资格χ与认可量v由冻结阈值T(s)、b(s)与eA、eR比较得到。
"""
from dataclasses import dataclass, field


@dataclass
class Recipe:
    recipe_id: str
    year: int
    steel_node: str
    grade: str
    is_steel: bool
    scrap_share: float
    opex_eur_per_unit: float
    eR: float                      # 责任账：tCO2e / 单位产品
    eA: float                      # 资格账
    e_sys_proc: float              # 系统账中的工序分量（未在他处计入）
    e_taxed: dict                  # 排放国 -> 被国内碳价覆盖的工序排放
    inputs: list = field(default_factory=list)      # (input_id, node, coef, destination)
    outputs: list = field(default_factory=list)     # (commodity, node, coef)
    capacity_use: list = field(default_factory=list)  # (pool_id, coef)
    chi: int = 0
    v: float = 0.0


def build(case):
    recipes = {}
    for (rid, year), row in case.recipes.items():
        reqs = case.requirements[(rid, year)]
        opex = next(float(r["coefficient"]) for r in reqs if r["requirement_type"] == "opex")
        inputs, outputs, caps = [], [], []
        for req in reqs:
            kind = req["requirement_type"]
            coef = float(req["coefficient"])
            if kind == "input":
                inputs.append((req["input_id"], req["input_node"], coef, req["destination_node"]))
            elif kind == "output":
                outputs.append((req["input_id"], req["input_node"], coef))
            elif kind == "capacity_pool":
                caps.append((req["input_id"], coef))
        comps = case.emission_components[(rid, year)]
        eR = sum(_factor(c) for c in comps if _flag(c["responsibility_included"]))
        eA = sum(_factor(c) for c in comps if _flag(c["eligibility_included"]))
        e_sys = sum(_factor(c) for c in comps
                    if _flag(c["system_included"]) and not _flag(c["included_elsewhere"]))
        e_taxed = {}
        for c in comps:
            share = _num_or_zero(c["ets_coverage_share"])
            if share > 0:
                country = c["emission_country"]
                e_taxed[country] = e_taxed.get(country, 0.0) + _factor(c) * share

        grade = row["grade_id"].strip()
        is_steel = bool(grade)
        chi, v = 0, 0.0
        if is_steel and row["certificate_class"].strip() != "NONE":
            rule = case.certificate_rules[("MAIN", year)]
            s = float(row["scrap_charge_share"])
            threshold = float(rule["t_primary"]) * (1 - s) + float(rule["t_scrap"]) * s
            benchmark = float(rule["b_primary"]) * (1 - s) + float(rule["b_scrap"]) * s
            if eA <= threshold:
                chi = 1
                v = max(0.0, benchmark - eR)
        recipes[(rid, year)] = Recipe(
            recipe_id=rid, year=year, steel_node=row["steel_node"], grade=grade, is_steel=is_steel,
            scrap_share=float(row["scrap_charge_share"]), opex_eur_per_unit=opex,
            eR=eR, eA=eA, e_sys_proc=e_sys, e_taxed=e_taxed,
            inputs=inputs, outputs=outputs, capacity_use=caps, chi=chi, v=v)
    return recipes


def _factor(component):
    return float(component["factor"])


def _flag(value):
    return str(value).strip() in ("1", "true", "True", "yes")


def _num_or_zero(value):
    if value is None or str(value).strip() == "":
        return 0.0
    return float(value)
