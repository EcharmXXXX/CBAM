"""运行 N / C / G(κ) 情景组并写出 §17.4 的最低结果表与比较摘要。"""
import csv
import json
from pathlib import Path

from ..model.solve import solve_case

SCENARIOS = ("N", "C", "G1", "G05")


def run_all(case, scenarios=SCENARIOS):
    solutions = {}
    for scenario in scenarios:
        solutions[scenario] = solve_case(case, scenario)
    return solutions


def _write(path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def write_tables(case, solutions, out_dir, *, revised_policy=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_solutions = solutions
    _write(out_dir / "solver_status.csv", ["scenario", "status", "objective_Meur", "max_violation_Mt"],
           [dict(scenario=s, status=sol.status,
                 objective_Meur=sol.objective_meur if sol.status == "optimal" else "",
                 max_violation_Mt=sol.diagnostics.get("max_violation_Mt", "")) for s,sol in solutions.items()])
    solutions = {s:sol for s,sol in solutions.items() if sol.status == "optimal"}

    production = []
    for scenario, sol in solutions.items():
        for (rid, t), value in sol.q.items():
            production.append(dict(scenario=scenario, year=t, recipe=rid, production_Mt=round(value, 6)))
    _write(out_dir / "production_by_recipe.csv",
           ["scenario", "year", "recipe", "production_Mt"], production)

    delivery = []
    for scenario, sol in solutions.items():
        for (rid, d, t), value in sol.x.items():
            if value > 1e-9:
                delivery.append(dict(scenario=scenario, year=t, recipe=rid, destination=d,
                                     delivered_Mt=round(value, 6)))
    _write(out_dir / "delivery.csv", ["scenario", "year", "recipe", "destination", "delivered_Mt"],
           delivery)

    capacity = []
    for scenario, sol in solutions.items():
        for (pool, t), (used, available) in sol.pool_usage.items():
            capacity.append(dict(scenario=scenario, year=t, pool=pool, used_Mt=round(used, 6),
                                 available_Mt=round(available, 6),
                                 utilisation=round(used / available, 4) if available else ""))
    _write(out_dir / "capacity_utilisation.csv",
           ["scenario", "year", "pool", "used_Mt", "available_Mt", "utilisation"], capacity)

    emissions = []
    for scenario, sol in solutions.items():
        for t, parts in sol.emissions.items():
            emissions.append(dict(scenario=scenario, year=t, **{k: round(v, 6) for k, v in parts.items()}))
    _write(out_dir / "emissions_sys.csv",
           ["scenario", "year", "proc", "supply", "transport", "total"], emissions)

    certificates = []
    for scenario, sol in solutions.items():
        for t in case.years:
            steel_backing = sum(v for (rid, tt), v in sol.z.items() if tt == t)
            row = dict(scenario=scenario, year=t,
                       price_eur_per_tCO2=round(sol.price.get(t, 0.0), 4),
                       cbam_payment_Meur=round(sol.annual_costs[t]["cbam_payment"], 6))
            if revised_policy:
                row.update(backing_steel_Mt=round(steel_backing, 6),
                           retired_MtCO2e=round(sol.S[t], 6),
                           net_border_responsibility_MtCO2e=round(sol.L[t], 6))
            else:
                row.update(kappa=sol.kappa, issued_Mcerts=round(steel_backing, 6),
                           S_MtCO2=round(sol.S[t], 6), L_MtCO2=round(sol.L[t], 6))
            certificates.append(row)
    measures = (["backing_steel_Mt", "retired_MtCO2e", "net_border_responsibility_MtCO2e"]
                if revised_policy else ["kappa", "issued_Mcerts", "S_MtCO2", "L_MtCO2"])
    _write(out_dir / "certificates_and_clearing.csv",
           ["scenario", "year", *measures, "price_eur_per_tCO2", "cbam_payment_Meur"], certificates)

    investment = []
    for scenario, sol in solutions.items():
        for (oid, vy), value in sol.inv.items():
            if value > 1e-9:
                investment.append(dict(scenario=scenario, option=oid, decision_year=vy,
                                       capacity_Mtpy=round(value, 6)))
    _write(out_dir / "investment.csv", ["scenario", "option", "decision_year", "capacity_Mtpy"],
           investment)

    payments = []
    for scenario, sol in solutions.items():
        for t in case.years:
            cert_transfer = sol.price.get(t, 0.0) * sol.S.get(t, 0.0)
            payments.append(dict(scenario=scenario, year=t,
                cbam_payment_Meur=round(sol.annual_costs[t]["cbam_payment"], 6),
                certificate_transfer_Meur=round(cert_transfer, 6),
                origin_carbon_tax_Meur=round(sol.annual_costs[t]["origin_carbon_tax"], 6)))
    _write(out_dir / "payments.csv",
           ["scenario", "year", "cbam_payment_Meur", "certificate_transfer_Meur",
            "origin_carbon_tax_Meur"], payments)

    annual = [dict(scenario=s, year=t, **parts) for s,sol in solutions.items() for t,parts in sol.annual_costs.items()]
    _write(out_dir / "annual_costs_undiscounted.csv",
           ["scenario", "year", "opex", "resource", "transport", "investment_net_of_residual", "origin_carbon_tax", "cbam_payment", "mrv", "unreplaced_border", "tariff"], annual)
    _write(out_dir / "capacity_rents.csv", ["scenario", "pool", "year", "EUR_per_capacity_unit_year"],
           [dict(scenario=s,pool=pool,year=t,EUR_per_capacity_unit_year=value) for s,sol in solutions.items() for (pool,t),value in sol.capacity_rent.items()])
    (out_dir / "diagnostics.json").write_text(json.dumps({s:sol.diagnostics for s,sol in all_solutions.items()}, ensure_ascii=False,indent=2),encoding="utf-8")
    _write_summary(case, all_solutions, out_dir / "summary.md")
    return out_dir / "summary.md"


def write_policy_tables(case, solutions, benchmarks, output):
    benchmark_fields = ["grade_id", "year", "loose", "normal", "strict", "strict_source"]
    _write(output / "certificate_benchmarks.csv", benchmark_fields,
           (dict(grade_id=g, year=t, **values) for (g, t), values in benchmarks.items()))

    shipments = []
    payments = []
    issuance = []
    comparisons = []
    reference = sum(row["total"] for row in solutions["C"].emissions.values())
    for scenario, sol in solutions.items():
        outcome = dict(scenario=scenario, status=sol.status, sys_MtCO2e="", delta_vs_CBAM_MtCO2e="",
                       eu_imports_Mt="", eu_domestic_deliveries_Mt="", matches_cbam_emissions="")
        if sol.status == "optimal":
            emissions = sum(row["total"] for row in sol.emissions.values())
            outcome.update(sys_MtCO2e=emissions, delta_vs_CBAM_MtCO2e=emissions-reference,
                           matches_cbam_emissions=emissions <= reference + 1e-7,
                           eu_imports_Mt=0., eu_domestic_deliveries_Mt=0.)
            for (rid, market, year), amount in sol.x.items():
                if market != case.eu_market:
                    continue
                key = ("eu_domestic_deliveries_Mt" if case.recipes[rid, year]["steel_node"] == case.eu_market
                       else "eu_imports_Mt")
                outcome[key] += amount
        comparisons.append(outcome)
        for (origin, destination, resource, year), shipped in sol.f.items():
            loss = float(case.transport[origin, destination, resource, year].get("loss_fraction") or 0)
            shipments.append(dict(scenario=scenario, year=year, origin=origin, destination=destination,
                                  resource=resource, shipped_Mt=shipped, delivered_Mt=shipped*(1-loss)))
        for (exporter, year), row in sol.exporter_payments.items():
            payments.append(dict(scenario=scenario, year=year, exporter=exporter,
                                 replaced_scope_liability_MtCO2e=row["net_liability"],
                                 cbam_and_retained_payment_Meur=row["cbam_payment"],
                                 certificate_purchase_Meur=row["certificate_purchase"]))
        for (recipe, year), row in sol.certificate_supply.items():
            revenue = row["issued_and_retired_MtCO2e"] * sol.price.get(year, 0.)
            issuance.append(dict(scenario=scenario, year=year, recipe=recipe, **row,
                                 gross_certificate_revenue_Meur=revenue))
    _write(output / "policy_comparison.csv", list(comparisons[0]), comparisons)
    _write(output / "resource_trade.csv",
           ["scenario", "year", "origin", "destination", "resource", "shipped_Mt", "delivered_Mt"], shipments)
    _write(output / "exporter_payments.csv",
           ["scenario", "year", "exporter", "replaced_scope_liability_MtCO2e",
            "cbam_and_retained_payment_Meur", "certificate_purchase_Meur"], payments)
    _write(output / "certificate_issuance.csv",
           ["scenario", "year", "recipe", "scrap_share", "eA_tco2e_per_tcs", "threshold_tco2e_per_tcs",
            "eR_tco2e_per_tcs", "value_tco2e_per_tcs", "compatible_steel_Mt", "transferable_share",
            "proportional_ceiling_MtCO2e", "backing_steel_Mt", "issued_and_retired_MtCO2e",
            "gross_certificate_revenue_Meur"], issuance)


def _write_summary(case, solutions, path):
    lines = ["# CBAM-GSC模型运行摘要", ""]
    statuses = {row.get("value_status", "unclassified") for row in case.recipes.values()}
    lines.append(f"年份：{case.years[0]}—{case.years[-1]}；输入状态：{', '.join(sorted(statuses))}。")
    if "scenario" in statuses:
        lines.append("包含情景/合成输入；结构运行不构成真实政策效果。详见运行清单。")
    lines.append("")
    lines.append(f"| 情景 | 累计SYS排放 MtCO2e | ΔE vs C | 目标成本 M€ | 资源成本代理 M€ | CBAM支出PV M€ | 证书价格({case.years[0]}) €/tCO2e |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    reference = None
    for scenario, sol in solutions.items():
        if sol.status != "optimal":
            continue
        cumulative = sum(part["total"] for part in sol.emissions.values())
        if scenario == "C":
            reference = cumulative
    for scenario, sol in solutions.items():
        if sol.status != "optimal":
            lines.append(f"| {scenario} | 未求得最优解：{sol.status} | | | | | |")
            continue
        cumulative = sum(part["total"] for part in sol.emissions.values())
        delta = "" if reference is None or scenario == "C" else round(cumulative - reference, 4)
        price0 = sol.price.get(case.years[0], 0.0)
        lines.append(f"| {scenario} | {cumulative:.4f} | {delta} | {sol.objective_meur:.2f} "
                     f"| {sol.costs['resource_cost_proxy']:.2f} | {sol.costs['cbam_payment']:.2f} "
                     f"| {price0:.2f} |")
    lines.append("")
    lines.append("说明：")
    lines.append("- 证书价格由出清约束对偶提取，未折现；N/C 情景无出清约束，价格为0。")
    lines.append("- 本摘要的目标与成本为全期折现合计；payments.csv和annual_costs_undiscounted.csv为逐年未折现现金流。")
    lines.append(f"- 累计排放为{case.years[0]}—{case.years[-1]}物理排放加总，不折现。")
    lines.append("- G情景若主要通过进口缩减满足出清，需同时报告欧盟产出与进口变化（§12.2）。")
    path.write_text("\n".join(lines), encoding="utf-8")
