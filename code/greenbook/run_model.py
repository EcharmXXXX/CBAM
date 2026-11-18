"""Dated, reproducible dynamic LP runs with optional nonmonotone policy search."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from datetime import datetime
from .data_model import Case
from .devlin_inputs import update_devlin_inputs
from .energy_inputs import update_energy_supply
from .make_dynamic_instance import write_dynamic_instance
from .model.solve import solve_case
from .analysis.compare import write_tables, write_policy_tables
from .analysis.certificate_benchmarks import BENCHMARK_TIERS, build_benchmarks, read_strict_benchmarks
from .analysis.policy_search import search
from .analysis.audit import audit_solution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance",type=Path)
    parser.add_argument("--synthetic-demo",action="store_true")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--demand-case")
    parser.add_argument("--energy-supply",type=Path,help="JSON with islanded/grid engineering supply blocks")
    parser.add_argument("--devlin-inputs",type=Path,help="JSON with DR pellets and DRI/HBI engineering recipes")
    parser.add_argument("--terminal-rule",choices=["remaining_life","zero"],default="remaining_life")
    parser.add_argument("--strict-benchmarks", type=Path,
                        help="European low-emission reference inputs (defaults to certificate_strict_benchmarks.csv in the instance)")
    parser.add_argument("--include-hbi-trade", action="store_true",
                        help="Open cross-border HBI in all current scenarios")
    parser.add_argument("--legacy-policy", action="store_true", help="Reproduce the old Stage A policy")
    parser.add_argument("--kappa-grid",type=float,nargs="*",default=[],
                        help="Legacy policy only; the new scenarios always retire one-for-one")
    args = parser.parse_args()
    if bool(args.instance) == bool(args.synthetic_demo):
        parser.error("Choose --instance or --synthetic-demo")
    args.output.mkdir(parents=True,exist_ok=True)
    legacy = args.legacy_policy or bool(args.kappa_grid)
    if legacy and args.strict_benchmarks:
        parser.error("The historical policy does not use a strict benchmark table")
    allow_hbi = legacy or args.include_hbi_trade
    instance = (write_dynamic_instance(args.output/"synthetic_input", revised_policy=not legacy)
                if args.synthetic_demo else args.instance)
    case = Case.load(instance,demand_case=args.demand_case)
    energy_report = None
    devlin_report = None
    if args.devlin_inputs:
        case, devlin_report, energy_report = update_devlin_inputs(case,args.devlin_inputs,energy_path=args.energy_supply)
    elif args.energy_supply:
        energy_report = update_energy_supply(case,args.energy_supply)
    settings = dict(terminal_rule=args.terminal_rule, allow_hbi_trade=allow_hbi, legacy_policy=legacy)
    sols = {scenario: solve_case(case, scenario, **settings) for scenario in ("N", "C")}
    if legacy:
        for scenario in ("G1", "G05"):
            sols[scenario] = solve_case(case, scenario, **settings)
    else:
        path = args.strict_benchmarks or instance / "certificate_strict_benchmarks.csv"
        if not path.is_file():
            parser.error(f"A comparable European strict benchmark is required: {path}")
        schedule = build_benchmarks(case, sols["C"], read_strict_benchmarks(path))
        for tier in BENCHMARK_TIERS:
            values = {key: row[tier] for key, row in schedule.items()}
            sols[f"G_{tier.upper()}"] = solve_case(case, f"G_{tier.upper()}", benchmarks=values, **settings)
        write_policy_tables(case, sols, schedule, args.output)
    write_tables(case,sols,args.output, revised_policy=not legacy)
    audits = {s:audit_solution(case,sol) for s,sol in sols.items()}
    (args.output/"independent_audit.json").write_text(json.dumps(audits,indent=2),encoding="utf-8")
    raw = {s:{name:[dict(key=list(key),value=float(value)) for key,value in getattr(sol,name).items()]
              for name in ("q","x","u","f","z","inv","y")} for s,sol in sols.items()}
    (args.output/"raw_solution_activities.json").write_text(json.dumps(raw,indent=2),encoding="utf-8")
    model_path = Path(__file__).parent
    manifest = dict(run_time=datetime.now().astimezone().isoformat(),model_version="dynamic_core_v1" if legacy else "dynamic_core_v2",
        years=case.years,instance=str(instance.resolve()),synthetic_demo=args.synthetic_demo,
        calibrated=False if args.synthetic_demo else "not established by this runner",
        terminal_rule=args.terminal_rule,python=platform.python_version(),
        packages={name:importlib.metadata.version(name) for name in ("numpy","scipy")},
        input_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in instance.glob("*.csv")},
        code_sha256={str(p.relative_to(model_path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in model_path.rglob("*.py")},
        units=dict(quantity="Mt product",resource_quantity="million units of table 13 quantity_unit",
            emissions="MtCO2e",objective="million EUR present value",annual_cashflow="million EUR undiscounted"),
        solver_status={s:sol.status for s,sol in sols.items()},
        independent_audit=audits,
        remaining_modules=["hourly energy input calibration", "endogenous retrofit and early closure", "cohort-attributed new-only certification", "real 2026 initial-state calibration"])
    if energy_report is not None:
        report_name = "energy_supply_diagnostics.json"
        (args.output/report_name).write_text(json.dumps(energy_report,ensure_ascii=False,indent=2),encoding="utf-8")
        manifest["energy_supply"] = dict(input=str(args.energy_supply.resolve()),
            sha256=hashlib.sha256(args.energy_supply.read_bytes()).hexdigest(), diagnostics=report_name)
    if devlin_report is not None:
        report_name = "devlin_engineering_inputs.json"
        stages = ("shipped", "at_border", "charged")
        devlin_report["hbi_flows_t"] = [
            dict(scenario=scenario, recipe_id=row["recipe_id"], year=row["year"], **{
                stage: row[f"hbi_{stage}_t_per_tcs"] * float(sol.q[(row["recipe_id"], row["year"])]) * 1e6
                for stage in stages
            })
            for scenario, sol in sols.items() if sol.status == "optimal"
            for row in devlin_report["recipes"] if row["route"] == "hbi_eaf"
        ]
        (args.output/report_name).write_text(json.dumps(devlin_report,ensure_ascii=False,indent=2),encoding="utf-8")
        manifest["devlin_inputs"] = dict(input=str(args.devlin_inputs.resolve()),
            sha256=hashlib.sha256(args.devlin_inputs.read_bytes()).hexdigest(), diagnostics=report_name)
    manifest["hbi_trade_enabled"] = allow_hbi
    if not legacy:
        manifest.update(policy="benchmark_tiers_one_for_one", strict_benchmark_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if args.kappa_grid:
        manifest["policy_search"] = search(case,args.kappa_grid,args.output,terminal_rule=args.terminal_rule)
    (args.output/"run_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(args.output),"statuses":manifest["solver_status"],"years":case.years},ensure_ascii=True))
    if any(sol.status != "optimal" or not audits[scenario]["passed"]
           for scenario, sol in sols.items() if scenario in ("N", "C") or sol.status == "optimal"):
        raise SystemExit(2)
    if any(sol.status != "optimal" and not sol.status.startswith("infeasible:") for sol in sols.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
