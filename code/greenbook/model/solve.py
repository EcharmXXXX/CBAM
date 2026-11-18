"""按情景组装线性规划并用HiGHS求解（§11.1）。

单位约定：数量变量用Mt，成本系数保持eur/t（eur/t × Mt = M€），
排放系数 tCO2e/t × Mt = MtCO2e。证书价格 = 出清行对偶 / β_t。

情景差异（§12.1）：
- N：无边境措施；
- C：目标函数加入CBAM付款（式12）；
- G(κ)：加入签发上限与出清硬约束（式14—15），无CBAM付款。
"""
from collections import defaultdict
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .build_recipes import build as build_recipes
from .build_border import build as build_border
from .build_dynamic import (build as build_dynamic,
                             is_available, terminal_residual)
from .build_certificate import build as build_certificate

from .resource_trade import build as build_resource_trade


@dataclass
class Solution:
    scenario: str
    status: str
    objective_meur: float
    q: dict = field(default_factory=dict)          # Mt
    x: dict = field(default_factory=dict)          # Mt
    u: dict = field(default_factory=dict)          # Mt
    z: dict = field(default_factory=dict)          # 百万份
    inv: dict = field(default_factory=dict)        # Mtpy
    y: dict = field(default_factory=dict)          # (crossing, t) -> Mt goods
    L: dict = field(default_factory=dict)           # t -> MtCO2e
    S: dict = field(default_factory=dict)           # t -> MtCO2e
    price: dict = field(default_factory=dict)        # t -> eur/tCO2e（未折现）
    pool_usage: dict = field(default_factory=dict)  # (pool, t) -> (used, available) Mt
    costs: dict = field(default_factory=dict)       # 分项折现成本 M€
    emissions: dict = field(default_factory=dict)   # t -> {proc, supply, transport, total} MtCO2e
    kappa: float = 0.0
    annual_costs: dict = field(default_factory=dict)  # undiscounted M EUR/year
    diagnostics: dict = field(default_factory=dict)
    capacity_rent: dict = field(default_factory=dict)
    f: dict = field(default_factory=dict)           # (origin, dest, resource, year) -> shipped Mt
    exporter_payments: dict = field(default_factory=dict)
    certificate_supply: dict = field(default_factory=dict)  # (recipe, year) -> issuance accounting


class _Vars:
    def __init__(self):
        self.keys = []
        self.pos = {}

    def add(self, key):
        if key not in self.pos:
            self.pos[key] = len(self.keys)
            self.keys.append(key)
        return self.pos[key]

    def __len__(self):
        return len(self.keys)


def solve_case(case, scenario, *, kappa_override=None, terminal_rule="remaining_life",
               benchmarks=None, allow_hbi_trade=True, legacy_policy=True):
    if terminal_rule not in ("remaining_life", "zero"):
        raise ValueError("Unknown terminal rule")
    revised = benchmarks is not None
    if revised and (not scenario.startswith("G_") or kappa_override is not None):
        raise ValueError("The benchmark policy uses fixed one-for-one retirement")
    if not legacy_policy and scenario.startswith("G") and not revised:
        raise ValueError("The benchmark policy requires a schedule for the certificate scenario")
    recipes = build_recipes(case, benchmarks=benchmarks, qualify=legacy_policy or revised)
    trades, arrivals, departures, supplies = build_resource_trade(case, recipes)
    crossings = build_border(case)
    pools, options = build_dynamic(case)
    certs = build_certificate(case, recipes)
    years = case.years
    if not years or len({key[2] for key in case.demand}) != 1:
        raise ValueError("Exactly one nonempty demand_case must be selected")
    if any(not np.isfinite(value) or value < 0 for value in case.demand.values()):
        raise ValueError("Demand must contain finite nonnegative quantities")
    final_year = years[-1]
    scale = 1e6

    rate = float(case.param("COMMON", "discount_rate"))
    beta = {t: (1 + rate) ** (-(t - years[0])) for t in years}
    origin_price = {(region, t): float(case.param("COMMON", "origin_carbon_price", region, year=t))
                    for region in case.regions for t in years}
    kappa = 1.0 if revised else float(
        kappa_override if kappa_override is not None else (case.param(scenario, "kappa") or 0.0)
    )
    if kappa < 0 or not np.isfinite(kappa):
        raise ValueError("Invalid kappa")
    if scenario not in ("N", "C") and not scenario.startswith("G"):
        raise ValueError(f"Unknown scenario: {scenario}")
    if scenario in ("N", "C") and kappa:
        raise ValueError("N and C cannot carry a certificate obligation")
    cbam_active = scenario == "C"
    clearing_active = kappa > 0

    # ---- 变量注册 ----
    var = _Vars()
    for (rid, t) in recipes:
        var.add(("q", rid, t))
    for (rid, t) in recipes:
        recipe = recipes[(rid, t)]
        if recipe.is_steel:
            for d in case.regions:
                var.add(("x", rid, d, t))
    for (oid, decision_year), option in options.items():
        var.add(("inv", oid, option.decision_year))
    for (node, res, block, t) in case.resources:
        var.add(("u", node, res, block, t))
    for (rid, t) in certs.eligible:
        var.add(("z", rid, t))
    for key in trades:
        var.add(("f", *key))
    n = len(var)

    def border_key(kind, origin, destination, year):
        if kind == "q":
            return ("q", origin, year)
        if kind == "f":
            return ("f", origin, *destination, year)
        return ("x", origin, destination, year)

    # ---- 目标函数 ----
    c = np.zeros(n)
    cost_opex = np.zeros(n)
    cost_tax = np.zeros(n)
    cost_resource = np.zeros(n)
    cost_transport = np.zeros(n)
    cost_invest = np.zeros(n)
    cost_mrv = np.zeros(n)
    cost_cbam = np.zeros(n)
    cost_retained = np.zeros(n)
    cost_tariff = np.zeros(n)

    for (rid, t), recipe in recipes.items():
        qi = var.pos[("q", rid, t)]
        opex = beta[t] * recipe.opex_eur_per_unit
        tax = beta[t] * sum(origin_price[(country, t)] * ef for country, ef in recipe.e_taxed.items())
        cost_opex[qi] += opex
        cost_tax[qi] += tax
        c[qi] += opex + tax
        # 跨节点投入品运费（如X→EU的HBI）
        for input_id, node, coef, destination in recipe.inputs:
            if destination and node != destination:
                arc = case.transport[(node, destination, input_id, t)]
                freight = beta[t] * coef * float(arc["private_freight_eur_per_t"])
                cost_transport[qi] += freight
                c[qi] += freight

    for (rid, t), recipe in recipes.items():
        if not recipe.is_steel:
            continue
        for d in case.regions:
            xi = var.pos[("x", rid, d, t)]
            arc = case.transport[(recipe.steel_node, d, "steel", t)]
            freight = beta[t] * float(arc["private_freight_eur_per_t"])
            cost_transport[xi] += freight
            c[xi] += freight

    for (node, res, block, t), row in case.resources.items():
        ui = var.pos[("u", node, res, block, t)]
        price = beta[t] * float(row["private_price_per_unit"])
        cost_resource[ui] += price
        c[ui] += price

    for key in trades:
        fi = var.pos[("f", *key)]
        freight = beta[key[-1]] * float(case.transport[key]["private_freight_eur_per_t"])
        cost_transport[fi] += freight
        c[fi] += freight

    for (oid, decision_year), option in options.items():
        ii = var.pos[("inv", oid, option.decision_year)]
        # §11.1：完整CAPEX按开工（决策年）只计一次，不另收年金；
        # 期末按剩余寿命比例退残值（§11.1），FOM按投运年份计。
        capex_cost = beta.get(option.decision_year, 1.0) * option.capex
        c[ii] += capex_cost
        cost_invest[ii] += capex_cost
        for t in years:
            if is_available(option, option.decision_year, t):
                c[ii] += beta[t] * option.fom
                cost_invest[ii] += beta[t] * option.fom
        residual = terminal_residual(option, option.decision_year, final_year) if terminal_rule == "remaining_life" else 0.
        c[ii] -= beta[final_year] * residual
        cost_invest[ii] -= beta[final_year] * residual

    for (rid, t) in certs.eligible:
        zi = var.pos[("z", rid, t)]
        fee = beta[t] * certs.mrv[t]
        cost_mrv[zi] += fee
        c[zi] += fee

    for (crossing_class, t), crossing in crossings.items():
        row = case.border_rules[(crossing_class, t)]
        for kind, rid, destination, coef in crossing.variable_terms():
            idx = var.pos[border_key(kind, rid, destination, t)]
            tariff = beta[t] * float(row.get("tariff_eur_per_t_goods") or 0.) * coef
            cost_tariff[idx] += tariff
            c[idx] += tariff
            if cbam_active or not crossing.replaced:
                pay = beta[t] * crossing.h * crossing.pay_price * coef
                vector = cost_cbam if crossing.replaced else cost_retained
                vector[idx] += pay
                c[idx] += pay

    # ---- 约束 ----
    eq_rows, eq_rhs = [], []
    ub_rows, ub_rhs = [], []
    clearing_row = {}
    capacity_rows = {}

    def add_eq(coefs, rhs):
        eq_rows.append(coefs)
        eq_rhs.append(rhs)

    def add_ub(coefs, rhs):
        ub_rows.append(coefs)
        ub_rhs.append(rhs)

    # 1) 需求平衡（式1）
    for (region, grade, demand_case, t), demand in case.demand.items():
        coefs = {}
        for rid in case.steel_recipe_ids:
            if (rid, t) not in recipes or recipes[(rid, t)].grade != grade:
                continue
            xi = var.pos[("x", rid, region, t)]
            coefs[xi] = coefs.get(xi, 0.0) + 1.0
        add_eq(coefs, demand / scale)

    # 2) q = Σ x
    for (rid, t), recipe in recipes.items():
        if not recipe.is_steel:
            continue
        qi = var.pos[("q", rid, t)]
        coefs = {qi: 1.0}
        for d in case.regions:
            xi = var.pos[("x", rid, d, t)]
            coefs[xi] = coefs.get(xi, 0.0) - 1.0
        add_eq(coefs, 0.0)

    # 3) 资源平衡（式5）：投入 = 供给块之和
    resource_keys = sorted(set(supplies) | set(arrivals))
    available_resources = set(resource_keys)
    produced_intermediates = {(node, commodity, year)
                              for (rid, year), recipe in recipes.items()
                              for commodity, node, _coef in recipe.outputs}
    for (rid, year), recipe in recipes.items():
        for commodity, node, _coef, destination in recipe.inputs:
            key = (node, commodity, year)
            if key not in available_resources and key not in produced_intermediates:
                raise ValueError(f"Missing resource supply or intermediate output for {rid}: {key}")
    for node, res, t in resource_keys:
        coefs = {}
        for (rid, tt), recipe in recipes.items():
            if tt != t:
                continue
            for input_id, input_node, coef, _dest in recipe.inputs:
                if input_id == res and input_node == node:
                    qi = var.pos[("q", rid, t)]
                    coefs[qi] = coefs.get(qi, 0.0) + coef
        pool = (node, res, t)
        domestic = {var.pos[("u", *key)]: -1.0 for key in supplies[pool]}
        exports = {var.pos[("f", *key)]: 1.0 for key in departures[pool]}
        coefs.update(domestic)
        coefs.update(exports)
        for key in arrivals[pool]:
            coefs[var.pos[("f", *key)]] = -trades[key][0]
        if coefs:
            add_eq(coefs, 0.0)
        if exports:
            add_ub(domestic | exports, 0.0)

    # Intermediate production and consumption share exactly one balance per
    # commodity/node/year, including local and cross-node consumption. Exogenous
    # supply for the same product is rejected to avoid duplicated accounting.
    if produced_intermediates & available_resources:
        raise ValueError("A commodity cannot have both implicit intermediate production and external supply blocks")
    for node, commodity, t in sorted(produced_intermediates):
        coefs = {}
        for (rid, tt), recipe in recipes.items():
            if tt != t:
                continue
            qi = var.pos[("q", rid, t)]
            for input_id, input_node, coef, _dest in recipe.inputs:
                if (input_id, input_node) == (commodity, node):
                    coefs[qi] = coefs.get(qi, 0.) - coef
            for out_id, out_node, coef in recipe.outputs:
                if (out_id, out_node) == (commodity, node):
                    coefs[qi] = coefs.get(qi, 0.) + coef
        add_eq(coefs, 0.)

    # 5) 共享能力（式4）：占用 ≤ 利用率 × (初始 + 新批次)
    for pool_id, pool in pools.items():
        for t in years:
            coefs = {}
            for (rid, tt), recipe in recipes.items():
                if tt != t:
                    continue
                for used_pool, coef in recipe.capacity_use:
                    if used_pool == pool_id:
                        qi = var.pos[("q", rid, t)]
                        coefs[qi] = coefs.get(qi, 0.0) + coef
            for (oid, decision_year), option in options.items():
                if option.pool_id == pool_id and is_available(option, option.decision_year, t):
                    ii = var.pos[("inv", oid, option.decision_year)]
                    coefs[ii] = coefs.get(ii, 0.0) - option.max_utilization
            add_ub(coefs, pool.available(t) / scale)
            capacity_rows[(pool_id, t)] = len(ub_rows) - 1

    # Shared annual construction limits across alternative investment options.
    for row in case.build_limits:
        if row["limit_kind"] != "new_build":
            raise ValueError("Only new_build shared limits are implemented")
        year = int(row["year"])
        if year not in years:
            continue
        coefs = {}
        linked = row.get("linked_option_id", "").strip()
        target = f"{row['node_id']}_{row['process_or_resource']}"
        for (oid, decision_year), option in options.items():
            if decision_year == year and (oid == linked if linked else option.pool_id == target):
                coefs[var.pos[("inv", oid, decision_year)]] = 1.
        if not coefs:
            raise ValueError(f"Build limit has no matching investment: {row['limit_id']}")
        add_ub(coefs, float(row["upper_value"]) / scale)

    # 6) 运输弧容量（钢与跨节点投入品）
    shared_corridors = {}
    for (origin, dest, commodity, t), arc in case.transport.items():
        capacity = float(arc["annual_capacity_t"])
        if not np.isfinite(capacity) or capacity < 0:
            raise ValueError("Transport capacity must be finite and nonnegative")
        coefs = {}
        if (origin, dest, commodity, t) in trades:
            coefs[var.pos[("f", origin, dest, commodity, t)]] = 1.0
        elif commodity == "steel":
            for rid in case.steel_recipe_ids:
                if (rid, t) not in recipes:
                    continue
                recipe = recipes[(rid, t)]
                if recipe.steel_node == origin:
                    xi = var.pos[("x", rid, dest, t)]
                    coefs[xi] = coefs.get(xi, 0.0) + 1.0
        else:
            for (rid, tt), recipe in recipes.items():
                if tt != t:
                    continue
                for input_id, input_node, coef, destination in recipe.inputs:
                    if input_id == commodity and input_node == origin and destination == dest:
                        qi = var.pos[("q", rid, t)]
                        coefs[qi] = coefs.get(qi, 0.0) + coef
        corridor = arc.get("shared_corridor_id", "").strip()
        if corridor:
            shared, limit = shared_corridors.setdefault((corridor, t), ({}, capacity))
            if capacity != limit:
                raise ValueError(f"Inconsistent shared transport capacity: {corridor}/{t}")
            for i, value in coefs.items():
                shared[i] = shared.get(i, 0.) + value
        elif coefs:
            add_ub(coefs, capacity / scale)
    for coefs, capacity in shared_corridors.values():
        if coefs:
            add_ub(coefs, capacity / scale)

    # 7) 签发上限（式14）
    for (rid, t), (chi, rho, v) in certs.eligible.items():
        zi = var.pos[("z", rid, t)]
        coefs = {zi: 1.0}
        for d in case.regions:
            if certs.attribute_use(rid, d, case, t) > 0:
                xi = var.pos[("x", rid, d, t)]
                coefs[xi] = coefs.get(xi, 0.0) - rho
        add_ub(coefs, 0.0)

    # Verified absolute rights are joint caps, not an extra subtraction from rho.
    for row in case.attribute_rights:
        t = int(row["year"])
        if t not in years:
            continue
        rid = row.get("recipe_id", "").strip()
        plant = row.get("plant_id", "").strip()
        mapped = [r for (r, year), recipe in recipes.items() if year == t and recipe.is_steel and
                  (r == rid if rid else bool(plant) and case.recipes[r, t].get("plant_id", "") == plant)]
        if not mapped:
            raise ValueError("Absolute attribute rights require a verified recipe_id or recipe-to-plant mapping")
        limit = float(row["available_rights_tcs"])
        if not np.isfinite(limit) or limit < 0:
            raise ValueError("Available attribute rights must be finite and nonnegative")
        linked = [r for r in mapped if (r, t) in certs.eligible]
        if linked:
            add_ub({var.pos[("z", r, t)]: 1. for r in linked}, limit / scale)

    # 8) 出清（式15）
    if clearing_active:
        for t in years:
            coefs = {}
            for (rid, tt), (chi, rho, v) in certs.eligible.items():
                if tt != t:
                    continue
                zi = var.pos[("z", rid, t)]
                coefs[zi] = coefs.get(zi, 0.0) - v
            for (crossing_class, tt), crossing in crossings.items():
                if tt != t or not crossing.replaced:
                    continue
                for kind, rid, destination, coef in crossing.variable_terms():
                    idx = var.pos[border_key(kind, rid, destination, t)]
                    coefs[idx] = coefs.get(idx, 0.0) + kappa * crossing.h * coef
            if revised:
                add_eq(coefs, 0.0)
                clearing_row[t] = len(eq_rows) - 1
            else:
                add_ub(coefs, 0.0)
                clearing_row[t] = len(ub_rows) - 1

    # ---- 边界 ----
    bounds = [(0, None)] * n
    demand_pairs = {(region, grade, year) for region, grade, _case, year in case.demand}
    for (rid, t), recipe in recipes.items():
        if recipe.is_steel:
            for region in case.regions:
                if (region, recipe.grade, t) not in demand_pairs:
                    bounds[var.pos[("x", rid, region, t)]] = (0., 0.)
    for key, (_yield, enabled) in trades.items():
        if not enabled:
            bounds[var.pos[("f", *key)]] = (0., 0.)
    if not allow_hbi_trade:
        for (rid, t), recipe in recipes.items():
            if any(dest and origin != dest and (res == "hbi" or res.startswith("hbi_"))
                   for res, origin, _coef, dest in recipe.inputs):
                bounds[var.pos[("q", rid, t)]] = (0., 0.)
    for (rid, t), (_chi, _rho, value) in certs.eligible.items():
        if not clearing_active or (revised and value == 0):
            bounds[var.pos[("z", rid, t)]] = (0., 0.)
    for (node, res, block, t), row in case.resources.items():
        ui = var.pos[("u", node, res, block, t)]
        bounds[ui] = (0.0, float(row["upper_quantity"]) / scale)
    for (oid, decision_year), option in options.items():
        ii = var.pos[("inv", oid, option.decision_year)]
        bounds[ii] = (0.0, option.build_limit / scale)

    # ---- 求解 ----
    a_eq = _dense(eq_rows, n)
    a_ub = _dense(ub_rows, n)
    res = linprog(c, A_ub=a_ub, b_ub=np.array(ub_rhs) if ub_rows else None, A_eq=a_eq, b_eq=np.array(eq_rhs) if eq_rows else None,
                  bounds=bounds, method="highs")
    if not res.success:
        status = {1: "limit_reached", 2: "infeasible", 3: "unbounded", 4: "solver_error"}.get(res.status, "failed")
        return Solution(scenario=scenario, status=f"{status}: {res.message}",
                        objective_meur=float("nan"), kappa=kappa,
                        diagnostics={"solver_status": int(res.status), "success": False})

    values = res.x
    get = lambda key: values[var.pos[key]]

    q = {key[1:]: get(key) for key in var.keys if key[0] == "q"}
    x = {key[1:]: get(key) for key in var.keys if key[0] == "x"}
    u = {key[1:]: get(key) for key in var.keys if key[0] == "u"}
    z = {key[1:]: get(key) for key in var.keys if key[0] == "z"}
    inv = {key[1:]: get(key) for key in var.keys if key[0] == "inv"}
    f = {key: get(("f", *key)) for key in trades}

    # ---- 责任量、出清量与价格 ----
    y, L, S, price = {}, {}, {}, {}
    for (crossing_class, t), crossing in crossings.items():
        total = 0.0
        for kind, rid, destination, coef in crossing.variable_terms():
            total += coef * get(border_key(kind, rid, destination, t))
        y[(crossing_class, t)] = total
    for t in years:
        L[t] = sum(crossings[(cc, tt)].h * y[(cc, t)] for (cc, tt) in crossings if tt == t and crossings[(cc,tt)].replaced)
        S[t] = sum(v * z.get((rid, tt), 0.0) for (rid, tt), (_c, _r, v) in certs.eligible.items()
                   if tt == t)
        if clearing_active:
            marginal = (res.eqlin if revised else res.ineqlin).marginals[clearing_row[t]]
            price[t] = max(0.0, -marginal / beta[t])

    exporter_payments = defaultdict(lambda: dict(net_liability=0., cbam_payment=0., certificate_purchase=0.))
    for (crossing_class, t), crossing in crossings.items():
        explicit_exporter = case.border_rules[crossing_class, t].get("exporter_region", "").strip()
        for kind, rid, destination, coefficient in crossing.variable_terms():
            if kind == "f":
                exporter = rid
            elif kind == "x":
                exporter = recipes[rid, t].steel_node
            else:
                exporter = case.recipes[rid, t]["iron_node"]
            entry = exporter_payments[(explicit_exporter or exporter, t)]
            liability = crossing.h * coefficient * get(border_key(kind, rid, destination, t))
            if crossing.replaced:
                entry["net_liability"] += liability
                entry["certificate_purchase"] += liability * kappa * price.get(t, 0.)
            if cbam_active or not crossing.replaced:
                entry["cbam_payment"] += liability * crossing.pay_price

    certificate_supply = {}
    for (rid, t), (_chi, rho, value) in certs.eligible.items():
        compatible = sum(x[rid, market, t] for market in case.regions
                         if certs.attribute_use(rid, market, case, t) > 0)
        recipe = recipes[rid, t]
        rule = case.certificate_rules["MAIN", t]
        threshold = float(rule["t_primary"]) * (1 - recipe.scrap_share) + float(rule["t_scrap"]) * recipe.scrap_share
        certificate_supply[rid, t] = dict(
            scrap_share=recipe.scrap_share, eA_tco2e_per_tcs=recipe.eA, threshold_tco2e_per_tcs=threshold,
            eR_tco2e_per_tcs=recipe.eR, value_tco2e_per_tcs=value, compatible_steel_Mt=compatible,
            transferable_share=rho, proportional_ceiling_MtCO2e=compatible * rho * value,
            backing_steel_Mt=z[rid, t], issued_and_retired_MtCO2e=z[rid, t] * value)

    # ---- 能力占用 ----
    pool_usage = {}
    for pool_id, pool in pools.items():
        for t in years:
            used = 0.0
            for (rid, tt), recipe in recipes.items():
                if tt == t:
                    for used_pool, coef in recipe.capacity_use:
                        if used_pool == pool_id:
                            used += coef * q[(rid, t)]
            available = pool.available(t) / scale
            for (oid, decision_year), option in options.items():
                if option.pool_id == pool_id and is_available(option, option.decision_year, t):
                    available += option.max_utilization * inv[(oid, option.decision_year)]
            pool_usage[(pool_id, t)] = (used, available)

    # ---- 成本分项（折现M€）----
    costs = {
        "opex": float(cost_opex @ values),
        "resource": float(cost_resource @ values),
        "transport": float(cost_transport @ values),
        "investment_net_of_residual": float(cost_invest @ values),
        "origin_carbon_tax": float(cost_tax @ values),
        "cbam_payment": float(cost_cbam @ values),
        "mrv": float(cost_mrv @ values),
        "unreplaced_border": float(cost_retained @ values),
        "tariff": float(cost_tariff @ values),
    }
    costs["resource_cost_proxy"] = (costs["opex"] + costs["resource"] + costs["transport"]
                                     + costs["investment_net_of_residual"] + costs["mrv"])

    # ---- 排放账（式9）----
    emissions = {}
    for t in years:
        proc = sum(recipe.e_sys_proc * q[(rid, t)] for (rid, tt), recipe in recipes.items() if tt == t)
        supply = sum(u[(node, res, block, t)] * float(row["emission_factor"])
                    for (node, res, block, tt), row in case.resources.items() if tt == t)
        transport = sum(amount * float(case.transport[key]["emissions_tco2e_per_t"])
                        for key, amount in f.items() if key[-1] == t)
        for rid in case.steel_recipe_ids:
            if (rid, t) not in recipes:
                continue
            recipe = recipes[(rid, t)]
            for d in case.regions:
                arc = case.transport[(recipe.steel_node, d, "steel", t)]
                transport += x[(rid, d, t)] * float(arc["emissions_tco2e_per_t"])
        for (rid, tt), recipe in recipes.items():
            if tt != t:
                continue
            for input_id, node, coef, dest in recipe.inputs:
                if dest and node != dest:
                    arc = case.transport[(node, dest, input_id, t)]
                    transport += coef * q[(rid, t)] * float(arc["emissions_tco2e_per_t"])
        emissions[t] = {"proc": proc, "supply": supply, "transport": transport,
                        "total": proc + supply + transport}

    components = {"opex": cost_opex, "resource": cost_resource, "transport": cost_transport,
                  "investment_net_of_residual": cost_invest, "origin_carbon_tax": cost_tax,
                  "cbam_payment": cost_cbam, "mrv": cost_mrv,
                  "unreplaced_border": cost_retained, "tariff": cost_tariff}
    annual_costs = {t: {name: 0. for name in components} for t in years}
    # Each variable is dated. Split investment's construction, annual FOM and
    # terminal residual explicitly rather than dumping its whole PV in year v.
    for key, idx in var.pos.items():
        if key[0] == "inv":
            continue
        t = key[-1]
        for name, vector in components.items():
            annual_costs[t][name] += vector[idx] * values[idx] / beta[t]
    for (oid, vy), option in options.items():
        amount = inv[(oid,vy)]
        annual_costs[vy]["investment_net_of_residual"] += option.capex * amount
        for t in years:
            if is_available(option, vy, t):
                annual_costs[t]["investment_net_of_residual"] += option.fom * amount
        residual = terminal_residual(option, vy, final_year) if terminal_rule == "remaining_life" else 0.
        annual_costs[final_year]["investment_net_of_residual"] -= residual * amount
    max_eq = float(np.max(np.abs(a_eq @ values - np.asarray(eq_rhs)))) if eq_rows else 0.
    max_ub = max(0., float(np.max(a_ub @ values - np.asarray(ub_rhs)))) if ub_rows else 0.
    bound_excess = max((max(0., values[i]-upper) for i,(_lower,upper) in enumerate(bounds) if upper is not None),default=0.)
    violation = max(max_eq, max_ub, max(0., float(-values.min())),float(bound_excess))
    objective_error = abs(sum(costs[k] for k in components) - float(res.fun))
    if violation > 1e-6 or objective_error > max(1e-5,abs(res.fun)*1e-9):
        raise RuntimeError("Solution feasibility or objective accounting check failed")
    diagnostics = dict(success=True, solver_status=int(res.status), variables=n,
                       equalities=len(eq_rows), inequalities=len(ub_rows),
                       max_violation_Mt=violation, objective_component_error_Meur=objective_error,
                       terminal_rule=terminal_rule,
                       price_not_identified_zero_obligation_years=[t for t in years if L[t] < 1e-9])
    capacity_rent = {key: max(0., -float(res.ineqlin.marginals[idx])/beta[key[1]])
                     for key,idx in capacity_rows.items()}
    return Solution(scenario=scenario, status="optimal", objective_meur=float(res.fun),
                    q=q, x=x, u=u, z=z, inv=inv, y=y, L=L, S=S, price=price,
                    pool_usage=pool_usage, costs=costs, emissions=emissions, kappa=kappa,
                    annual_costs=annual_costs, diagnostics=diagnostics, capacity_rent=capacity_rent,
                    f=f, exporter_payments=dict(exporter_payments), certificate_supply=certificate_supply)


def _dense(rows, n):
    if not rows:
        return None
    row_ids, columns, data = [], [], []
    for i, coefs in enumerate(rows):
        for j, value in coefs.items():
            if value:
                row_ids.append(i)
                columns.append(j)
                data.append(value)
    return coo_matrix((data, (row_ids, columns)), shape=(len(rows), n)).tocsr()
