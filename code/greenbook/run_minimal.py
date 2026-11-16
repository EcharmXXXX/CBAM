"""阶段A最小实例入口：生成实例数据、运行N/C/G1/G05并写出结果表。

用法（在 code/ 目录下）：
    python -m greenbook.run_minimal
"""
from pathlib import Path

from .make_instance import write_instance
from .data_model import Case
from .analysis.compare import run_all, write_tables

RESULTS = Path(__file__).resolve().parents[1] / "results" / "stage_a_minimal"


def main():
    instance_dir = Path(__file__).parent / "instance"
    if not (instance_dir / "11_recipes.csv").exists():
        write_instance(instance_dir)
    case = Case.load(instance_dir)
    solutions = run_all(case)
    summary = write_tables(case, solutions, RESULTS)

    print(f"results written to {RESULTS}")
    print("--- summary ---")
    print(summary.read_text(encoding="utf-8"))
    for scenario, sol in solutions.items():
        cumulative = sum(part["total"] for part in sol.emissions.values())
        print(f"{scenario}: status={sol.status}, cumulative SYS={cumulative:.4f} MtCO2e, "
              f"objective={sol.objective_meur:.2f} M€")


if __name__ == "__main__":
    main()
