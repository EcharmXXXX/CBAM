"""运行 N / C / G(κ) 情景组并写出 §17.4 的最低结果表与比较摘要。"""
import csv
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


def write_tables(case, solutions, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
            issued = sum(v for (rid, tt), v in sol.z.items() if tt == t)
            certificates.append(dict(scenario=scenario, year=t, kappa=sol.kappa,
                issued_Mcerts=round(issued, 6), S_MtCO2=round(sol.S.get(t, 0.0), 6),
                L_MtCO2=round(sol.L.get(t, 0.0), 6),
                price_eur_per_tCO2=round(sol.price.get(t, 0.0), 4),
                cbam_payment_Meur=round(
                    sol.costs.get("cbam_payment", 0.0), 6) if t == case.years[0] else ""))
    _write(out_dir / "certificates_and_clearing.csv",
           ["scenario", "year", "kappa", "issued_Mcerts", "S_MtCO2", "L_MtCO2",
            "price_eur_per_tCO2", "cbam_payment_Meur"], certificates)

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
                cbam_payment_Meur=round(sol.costs["cbam_payment"], 6) if t == case.years[0] else 0.0,
                certificate_transfer_Meur=round(cert_transfer, 6),
                origin_carbon_tax_Meur=round(sol.costs["origin_carbon_tax"], 6) if t == case.years[0] else 0.0))
    _write(out_dir / "payments.csv",
           ["scenario", "year", "cbam_payment_Meur", "certificate_transfer_Meur",
            "origin_carbon_tax_Meur"], payments)

    _write_summary(case, solutions, out_dir / "summary.md")
    return out_dir / "summary.md"


def _write_summary(case, solutions, path):
    lines = ["# 阶段A最小实例：情景比较摘要", ""]
    lines.append("玩具数据（TOY_STAGE_A），仅证明结构，不代表政策效果。")
    lines.append("")
    lines.append("| 情景 | 累计SYS排放 MtCO2e | ΔE vs C | 目标成本 M€ | 资源成本代理 M€ | CBAM支出 M€ | 证书价格(2027) €/tCO2e |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    reference = None
    for scenario, sol in solutions.items():
        cumulative = sum(part["total"] for part in sol.emissions.values())
        if scenario == "C":
            reference = cumulative
    for scenario, sol in solutions.items():
        cumulative = sum(part["total"] for part in sol.emissions.values())
        delta = "" if reference is None or scenario == "C" else round(cumulative - reference, 4)
        price0 = sol.price.get(case.years[0], 0.0)
        lines.append(f"| {scenario} | {cumulative:.4f} | {delta} | {sol.objective_meur:.2f} "
                     f"| {sol.costs['resource_cost_proxy']:.2f} | {sol.costs['cbam_payment']:.2f} "
                     f"| {price0:.2f} |")
    lines.append("")
    lines.append("说明：")
    lines.append("- 证书价格由出清约束对偶提取，未折现；N/C 情景无出清约束，价格为0。")
    lines.append("- CBAM支出与原产地碳税为三年折现合计。")
    lines.append("- 累计排放为2027—2029物理排放加总，不折现（§1）。")
    lines.append("- G情景若主要通过进口缩减满足出清，需同时报告欧盟产出与进口变化（§12.2）。")
    path.write_text("\n".join(lines), encoding="utf-8")
