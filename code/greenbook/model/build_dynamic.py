"""Annual asset survival, new-build cohorts and conservative terminal value.

Full CAPEX is paid once at decision; operating FOM during service. Natural and
announced retirement are supported. Endogenous closure and conversion require
additional source-vintage constraints and cannot be supplied as new builds.
"""
from dataclasses import dataclass, field


@dataclass
class Pool:
    pool_id: str
    initial_capacity: float
    utilization: float
    available_by_year: dict = field(default_factory=dict)

    def available(self, year):
        return self.available_by_year.get(year, 0.)


@dataclass
class InvestmentOption:
    option_id: str
    pool_id: str
    capex: float
    fom: float
    lead_years: int
    life_years: int
    decision_year: int
    build_limit: float
    max_utilization: float


def build(case):
    pools = {}
    for asset in case.assets:
        pool = pools.setdefault(asset["pool_id"], Pool(asset["pool_id"], 0., 1.))
        capacity = float(asset["capacity_tpy"]) * float(asset.get("scope_share") or 1.)
        utilization = float(asset["max_utilization"])
        start = int(asset["start_year"])
        life = int(asset["design_life_years"])
        stop = int(asset["retirement_year"]) if asset.get("retirement_year", "").strip() else start + life
        if capacity < 0 or not 0 <= utilization <= 1 or stop < start or life <= 0:
            raise ValueError(f"Invalid initial asset: {asset['asset_id']}")
        status = asset.get("status", "operating")
        if status not in ("operating", "operating_pre_retirement", "construction", "committed", "retired", "mothballed", "cancelled"):
            raise ValueError(f"Unrecognised asset status: {status}")
        for year in case.years:
            if start <= year < stop and status not in ("retired", "mothballed", "cancelled"):
                pool.available_by_year[year] = pool.available(year) + utilization * capacity
        if start <= case.years[0] < stop and status not in ("retired", "mothballed", "cancelled"):
            pool.initial_capacity += capacity
    for reqs in case.requirements.values():
        for req in reqs:
            if req["requirement_type"] == "capacity_pool":
                key = req["input_id"]
                pools.setdefault(key, Pool(key, 0., 1.))
    options = {}
    for (option_id, year), row in case.investments.items():
        limit = case.param("COMMON", "build_limit", option_id, year=year)
        if limit is None:
            raise ValueError(f"Missing build limit: {option_id}/{year}")
        option = InvestmentOption(option_id, f"{row['node_id']}_{row['target_process']}",
            float(row["capex_eur_per_tpy"]), float(row["fom_eur_per_tpy_year"]),
            int(row["lead_years"]), int(row["life_years"]), year, float(limit), float(row["max_utilization"]))
        if option.life_years <= 0 or option.lead_years < 0 or option.build_limit < 0 or option.capex < 0 or not 0 <= option.max_utilization <= 1:
            raise ValueError(f"Invalid investment batch: {option_id}/{year}")
        if year not in case.years:
            raise ValueError("Use the fixed committed-asset pipeline for pre-horizon investments")
        pools.setdefault(option.pool_id, Pool(option.pool_id, 0., 1.))
        options[(option_id, year)] = option
    return pools, options


def commission_year(option, decision_year):
    return decision_year + option.lead_years


def is_available(option, decision_year, year):
    start = commission_year(option, decision_year)
    return start <= year < start + option.life_years


def terminal_residual(option, decision_year, final_year):
    """Uncommissioned assets get zero; construction-in-progress is not refunded."""
    start = commission_year(option, decision_year)
    if start > final_year:
        return 0.
    remaining = max(0., option.life_years - (final_year - start + 1))
    return option.capex * min(1., remaining / option.life_years)
