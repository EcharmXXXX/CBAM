"""按情景组装线性规划并用HiGHS求解（§11.1）。

单位约定：数量变量用Mt，成本系数保持eur/t（eur/t × Mt = M€），
排放系数 tCO2e/t × Mt = MtCO2e。证书价格 = 出清行对偶 / β_t。

情景差异（§12.1）：
- N：无边境措施；
- C：目标函数加入CBAM付款（式12）；
- G(κ)：加入签发上限与出清硬约束（式14—15），无CBAM付款。
"""
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import linprog

from .build_recipes import build as build_recipes
from .build_border import build as build_border
from .build_dynamic import (build as build_dynamic,
                             is_available, terminal_residual)
from .build_certificate import build as build_certificate

LARGE_ARC = 400_000_000.0  # t；超过此值的运输弧不设容量行


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


def solve_case(case, scenario):
    recipes = build_recipes(case)
    crossings = build_border(case)
    pools, options = build_dynamic(case)
    certs = build_certificate(case, recipes)
    years = case.years
    final_year = years[-1]
    scale = 1e6

    rate = float(case.param("COMMON", "discount_rate"))
    beta = {t: (1 + rate) ** (-(t - years[0])) for t in years}
    origin_price = {region: float(case.param("COMMON", "origin_carbon_price", region))
                    for region in case.regions}
    kappa = float(case.param(scenario, "kappa") or 0.0)
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
    for oid, option in options.items():
        var.add(("inv", oid, option.decision_year))
    for (node, res, block, t) in case.resources:
        var.add(("u", node, res, block, t))
    for (rid, t) in certs.eligible:
        var.add(("z", rid, t))
    n = len(var)

    # ---- 目标函数 ----
    c = np.zeros(n)
    cost_opex = np.zeros(n)
    cost_tax = np.zeros(n)
    cost_resource = np.zeros(n)
    cost_transport = np.zeros(n)
    cost_invest = np.zeros(n)
    cost_mrv = np.zeros(n)
    cost_cbam = np.zeros(n)

    for (rid, t), recipe in recipes.items():
        qi = var.pos[("q", rid, t)]
        opex = beta[t] * recipe.opex_eur_per_unit
        tax = beta[t] * sum(origin_price[country] * ef for country, ef in recipe.e_taxed.items())
        cost_opex[qi] += opex
        cost_tax[qi] += tax
        c[qi] += opex + tax
        # 跨节点投入品运费（如X→EU的HBI）
        for input_id, node, coef, destination in recipe.inputs:
            if destination and node != destination:
                arc = case.transport[(node, destination, input_id, t)]
                cost_transport[qi] += beta[t] * coef * float(arc["private_freight_eur_per_t"])

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

    for oid, option in options.items():
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
        residual = terminal_residual(option, option.decision_year, final_year)
        c[ii] -= beta[final_year] * residual
        cost_invest[ii] -= beta[final_year] * residual

    for (rid, t) in certs.eligible:
        zi = var.pos[("z", rid, t)]
        fee = beta[t] * certs.mrv[t]
        cost_mrv[zi] += fee
        c[zi] += fee

    if cbam_active:
        for (crossing_class, t), crossing in crossings.items():
            for kind, rid, destination, coef in crossing.variable_terms():
                if kind == "q":
                    idx = var.pos[("q", rid, t)]
                else:
                    idx = var.pos[("x", rid, destination, t)]
                pay = beta[t] * crossing.h * crossing.pay_price * coef
                cost_cbam[idx] += pay
                c[idx] += pay

    # ---- 约束 ----
    eq_rows, eq_rhs = [], []
    ub_rows, ub_rhs = [], []
    clearing_row = {}

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
    resource_keys = sorted({(node, res, t) for (node, res, block, t) in case.resources})
    for node, res, t in resource_keys:
        coefs = {}
        for (rid, tt), recipe in recipes.items():
            if tt != t:
                continue
            for input_id, input_node, coef, _dest in recipe.inputs:
                if input_id == res and input_node == node:
                    qi = var.pos[("q", rid, t)]
                    coefs[qi] = coefs.get(qi, 0.0) + coef
        for (n2, r2, block, t2) in case.resources:
            if (n2, r2, t2) == (node, res, t):
                ui = var.pos[("u", node, res, block, t)]
                coefs[ui] = coefs.get(ui, 0.0) - 1.0
        if coefs:
            add_eq(coefs, 0.0)

    # 4) 中间品平衡（HBI，式6）：跨节点投入品 产出 = 消费；本地投入走资源平衡
    intermediates = sorted({(input_id, node, t)
                            for (rid, t2), recipe in recipes.items() for t in years
                            for (input_id, node, _c, dest) in recipe.inputs
                            if dest and node != dest})
    for commodity, node, t in intermediates:
        coefs = {}
        for (rid, tt), recipe in recipes.items():
            if tt != t:
                continue
            for input_id, input_node, coef, dest in recipe.inputs:
                if input_id == commodity and input_node == node and dest and dest != input_node:
                    qi = var.pos[("q", rid, t)]
                    coefs[qi] = coefs.get(qi, 0.0) - coef
            for out_id, out_node, coef in recipe.outputs:
                if out_id == commodity and out_node == node:
                    qi = var.pos[("q", rid, t)]
                    coefs[qi] = coefs.get(qi, 0.0) + coef
        if coefs:
            add_eq(coefs, 0.0)

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
            if not coefs:
                continue
            for oid, option in options.items():
                if option.pool_id == pool_id and is_available(option, option.decision_year, t):
                    ii = var.pos[("inv", oid, option.decision_year)]
                    coefs[ii] = coefs.get(ii, 0.0) - option.max_utilization
            add_ub(coefs, pool.utilization * pool.initial_capacity / scale)

    # 6) 运输弧容量（钢与跨节点投入品）
    for (origin, dest, commodity, t), arc in case.transport.items():
        capacity = float(arc["annual_capacity_t"])
        if capacity >= LARGE_ARC:
            continue
        coefs = {}
        if commodity == "steel":
            for rid in case.steel_recipe_ids:
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
        if coefs:
            add_ub(coefs, capacity / scale)

    # 7) 签发上限（式14）
    for (rid, t), (chi, rho, v) in certs.eligible.items():
        zi = var.pos[("z", rid, t)]
        coefs = {zi: 1.0}
        for d in case.regions:
            if certs.attribute_use(rid, d, case) > 0:
                xi = var.pos[("x", rid, d, t)]
                coefs[xi] = coefs.get(xi, 0.0) - rho
        add_ub(coefs, 0.0)

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
                if tt != t:
                    continue
                for kind, rid, destination, coef in crossing.variable_terms():
                    if kind == "q":
                        idx = var.pos[("q", rid, t)]
                    else:
                        idx = var.pos[("x", rid, destination, t)]
                    coefs[idx] = coefs.get(idx, 0.0) + kappa * crossing.h * coef
            add_ub(coefs, 0.0)
            clearing_row[t] = len(ub_rows) - 1

    # ---- 边界 ----
    bounds = [(0, None)] * n
    for (node, res, block, t), row in case.resources.items():
        ui = var.pos[("u", node, res, block, t)]
        bounds[ui] = (0.0, float(row["upper_quantity"]) / scale)
    for oid, option in options.items():
        ii = var.pos[("inv", oid, option.decision_year)]
        bounds[ii] = (0.0, option.build_limit / scale)

    # ---- 求解 ----
    a_eq = _dense(eq_rows, n)
    a_ub = _dense(ub_rows, n)
    res = linprog(c, A_ub=a_ub, b_ub=np.array(ub_rhs), A_eq=a_eq, b_eq=np.array(eq_rhs),
                  bounds=bounds, method="highs")
    if not res.success:
        return Solution(scenario=scenario, status=f"infeasible: {res.message}",
                        objective_meur=float("nan"), kappa=kappa)

    values = res.x
    get = lambda key: values[var.pos[key]]

    q = {key[1:]: get(key) for key in var.keys if key[0] == "q"}
    x = {key[1:]: get(key) for key in var.keys if key[0] == "x"}
    u = {key[1:]: get(key) for key in var.keys if key[0] == "u"}
    z = {key[1:]: get(key) for key in var.keys if key[0] == "z"}
    inv = {key[1:]: get(key) for key in var.keys if key[0] == "inv"}

    # ---- 责任量、出清量与价格 ----
    y, L, S, price = {}, {}, {}, {}
    for (crossing_class, t), crossing in crossings.items():
        total = 0.0
        for kind, rid, destination, coef in crossing.variable_terms():
            key = (rid, destination, t) if kind == "x" else (rid, t)
            total += coef * (x.get(key) if kind == "x" else q.get(key, 0.0))
        y[(crossing_class, t)] = total
    for t in years:
        L[t] = sum(crossings[(cc, tt)].h * y[(cc, t)] for (cc, tt) in crossings if tt == t)
        S[t] = sum(v * z.get((rid, tt), 0.0) for (rid, tt), (_c, _r, v) in certs.eligible.items()
                   if tt == t)
        if clearing_active:
            marginal = res.ineqlin.marginals[clearing_row[t]]
            price[t] = max(0.0, -marginal / beta[t])

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
            available = pool.utilization * pool.initial_capacity / scale
            for oid, option in options.items():
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
    }
    costs["resource_cost_proxy"] = (costs["opex"] + costs["resource"] + costs["transport"]
                                     + costs["investment_net_of_residual"] + costs["mrv"])

    # ---- 排放账（式9）----
    emissions = {}
    for t in years:
        proc = sum(recipe.e_sys_proc * q[(rid, t)] for (rid, tt), recipe in recipes.items() if tt == t)
        supply = sum(u[(node, res, block, t)] * float(row["emission_factor"])
                    for (node, res, block, tt), row in case.resources.items() if tt == t)
        transport = 0.0
        for rid in case.steel_recipe_ids:
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

    return Solution(scenario=scenario, status="optimal", objective_meur=float(res.fun),
                    q=q, x=x, u=u, z=z, inv=inv, y=y, L=L, S=S, price=price,
                    pool_usage=pool_usage, costs=costs, emissions=emissions, kappa=kappa)


def _dense(rows, n):
    if not rows:
        return None
    matrix = np.zeros((len(rows), n))
    for i, coefs in enumerate(rows):
        for j, value in coefs.items():
            matrix[i, j] = value
    return matrix
