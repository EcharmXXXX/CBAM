"""由11/12/19/21表构造配方：投入、共享能力占用、三本账排放与证书资格。

结构要点（§17.3）：
- EAF等共享池由多个配方共同占用，占用系数之和受单一能力约束；
- HBI经R_X_HBIPROD中间活动供给，同一吨HBI只形成一次粗钢；
- 排放分量含 included_elsewhere 标志，物理系统账不重复计入前体；
- 资格χ与认可量v由冻结阈值T(s)、b(s)与eA、eR比较得到。
"""
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from math import isfinite


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


def build(case, *, benchmarks=None, qualify=True):
    recipes = {}
    hydrogen_eligibility = _hydrogen_eligibility(case)
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
            if not 0 <= share <= 1:
                raise ValueError("ETS coverage share must lie between zero and one")
            if share and _flag(c["included_elsewhere"]):
                raise ValueError("Already counted precursor emissions cannot receive a second origin carbon charge")
            if share > 0:
                country = c["emission_country"]
                e_taxed[country] = e_taxed.get(country, 0.0) + _factor(c) * share

        grade = row["grade_id"].strip()
        is_steel = bool(grade)
        chi, v = 0, 0.0
        if qualify and is_steel and row["certificate_class"].strip() != "NONE":
            _, green_hydrogen_ok, electricity_emissions = hydrogen_eligibility(rid, year)
            eA += electricity_emissions
            rule = case.certificate_rules[("MAIN", year)]
            s = float(row["scrap_charge_share"])
            if not 0 <= s <= 1:
                raise ValueError(f"Invalid scrap share for {rid}/{year}")
            threshold = float(rule["t_primary"]) * (1 - s) + float(rule["t_scrap"]) * s
            if benchmarks is None:
                benchmark = float(rule["b_primary"]) * (1 - s) + float(rule["b_scrap"]) * s
            else:
                benchmark = benchmarks[(grade, year)]
            if green_hydrogen_ok and eA <= threshold:
                chi = 1
                v = max(0.0, benchmark - eR)
        recipes[(rid, year)] = Recipe(
            recipe_id=rid, year=year, steel_node=row["steel_node"], grade=grade, is_steel=is_steel,
            scrap_share=float(row["scrap_charge_share"]), opex_eur_per_unit=opex,
            eR=eR, eA=eA, e_sys_proc=e_sys, e_taxed=e_taxed,
            inputs=inputs, outputs=outputs, capacity_use=caps, chi=chi, v=v)
    return recipes


def _hydrogen_eligibility(case):
    """Trace renewable electrolysis through the physical hydrogen and HBI balances.

    The resource ID is the physical supply group. Split IDs by provenance and
    electricity intensity so a qualifying recipe cannot consume another group.
    """
    hydrogen_blocks = defaultdict(list)
    hbi_producers = defaultdict(list)
    for (node, resource, _block, year), row in case.resources.items():
        if resource == "h2" or resource.startswith("h2_") or row.get("resource_class") in ("h2", "hydrogen"):
            hydrogen_blocks[(node, resource, year)].append(row)
    for rid, year in case.recipes:
        for req in case.requirements[(rid, year)]:
            product = req["input_id"]
            if req["requirement_type"] == "output" and _is_hbi(product):
                hbi_producers[(req["input_node"], product, year)].append(
                    (rid, float(req["coefficient"])))

    @lru_cache(maxsize=None)
    def trace(rid, year, downstream=()):
        if rid in downstream:
            return True, False, 0.0
        iron_tech = case.recipes[(rid, year)].get("iron_tech", "").upper()
        route_requires_trace = "H2" in iron_tech or iron_tech.startswith("HBI")
        has_traced_input = False
        uses_hydrogen = False
        all_green = True
        electricity = 0.0
        for req in case.requirements[(rid, year)]:
            if req["requirement_type"] != "input" or float(req["coefficient"]) <= 0:
                continue
            resource = req["input_id"]
            key = (req["input_node"], resource, year)
            if resource == "h2" or resource.startswith("h2_") or key in hydrogen_blocks:
                has_traced_input = True
                uses_hydrogen = True
                factor = _green_electricity_factor(hydrogen_blocks.get(key, ()))
            elif _is_hbi(resource):
                has_traced_input = True
                candidates = []
                for producer, output in hbi_producers.get(key, ()):
                    used, eligible, upstream_electricity = trace(producer, year, (*downstream, rid))
                    candidates.append((used, eligible and output > 0,
                                       upstream_electricity / output if output > 0 else 0.0))
                # A single HBI pool may serve multiple recipes. Every possible
                # producer must be traceable, with the same factor per tonne.
                if candidates and all(not used and eligible for used, eligible, _ in candidates):
                    continue
                uses_hydrogen = True
                factors = {value for _, _, value in candidates}
                factor = (next(iter(factors)) if len(factors) == 1
                          and all(used and eligible for used, eligible, _ in candidates) else None)
            else:
                continue
            if factor is None:
                all_green = False
            else:
                electricity += float(req["coefficient"]) * factor
        unknown_route = route_requires_trace and not has_traced_input
        return uses_hydrogen or unknown_route, all_green and not unknown_route, electricity

    return trace


def _is_hbi(resource):
    return resource == "hbi" or resource.startswith("hbi_")


def _green_electricity_factor(rows):
    factors = set()
    for row in rows:
        if (row.get("hydrogen_production_method") != "water_electrolysis"
                or not (row.get("renewable_power_evidence_id") or "").strip()):
            return None
        value = row.get("electricity_emissions_tco2e_per_th2")
        if value is None or str(value).strip() == "":
            return None
        factor = float(value)
        if not isfinite(factor) or factor < 0:
            raise ValueError("Hydrogen electricity emissions must be finite and nonnegative")
        factors.add(factor)
    return next(iter(factors)) if len(factors) == 1 else None


def _factor(component):
    return float(component["factor"])


def _flag(value):
    return str(value).strip() in ("1", "true", "True", "yes")


def _num_or_zero(value):
    if value is None or str(value).strip() == "":
        raise ValueError("Missing ETS coverage share; explicitly supply zero for uncovered emissions")
    return float(value)
