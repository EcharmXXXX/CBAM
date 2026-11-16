"""初始资产批次、投资选项与可用性（§7）。

- 初始池能力 = 04表资产汇总；利用率取池内最小值；
- 新投资批次：决策年+建设期投产，寿命内可用；
- 目标函数对年内批次收年金与FOM，期末按剩余寿命比例给残值。
"""
from dataclasses import dataclass


@dataclass
class Pool:
    pool_id: str
    initial_capacity: float   # tpy
    utilization: float


@dataclass
class InvestmentOption:
    option_id: str
    pool_id: str
    capex: float              # eur per tpy
    fom: float                # eur per tpy per year
    lead_years: int
    life_years: int
    decision_year: int
    build_limit: float        # tpy
    max_utilization: float


def annuity_factor(rate, life):
    return rate / (1 - (1 + rate) ** (-life))


def build(case):
    pools = {}
    for asset in case.assets:
        pool = pools.setdefault(asset["pool_id"],
                                Pool(asset["pool_id"], 0.0, float(asset["max_utilization"])))
        pool.initial_capacity += float(asset["capacity_tpy"])
        pool.utilization = min(pool.utilization, float(asset["max_utilization"]))

    # 无初始资产的池（如 EU_DRI）也要建：配方占用与投资产能需要约束
    for reqs in case.requirements.values():
        for req in reqs:
            if req["requirement_type"] == "capacity_pool" and req["input_id"] not in pools:
                pools[req["input_id"]] = Pool(req["input_id"], 0.0, 1.0)

    options = {}
    for option_id, row in case.investments.items():
        limit = case.param("COMMON", "build_limit", option_id)
        options[option_id] = InvestmentOption(
            option_id=option_id,
            pool_id=f"{row['node_id']}_{row['target_process']}",
            capex=float(row["capex_eur_per_tpy"]),
            fom=float(row["fom_eur_per_tpy_year"]),
            lead_years=int(row["lead_years"]),
            life_years=int(row["life_years"]),
            decision_year=int(row["year"]),
            build_limit=float(limit),
            max_utilization=float(row["max_utilization"]))
    return pools, options


def commission_year(option, decision_year):
    return decision_year + option.lead_years


def is_available(option, decision_year, year):
    start = commission_year(option, decision_year)
    return start <= year < start + option.life_years


def terminal_residual(option, decision_year, final_year):
    """期末剩余寿命比例 × CAPEX，作为共同终端残值（§11.1）。"""
    start = commission_year(option, decision_year)
    years_used = final_year - start + 1
    remaining = max(0.0, option.life_years - years_used)
    return option.capex * remaining / option.life_years
