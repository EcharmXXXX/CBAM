"""Evaluate a stated kappa grid without assuming monotone emissions responses."""
from .compare import _write
from ..model.solve import solve_case


def search(case, grid, output, *, terminal_rule="remaining_life"):
    if not grid:
        raise ValueError("Kappa grid cannot be empty")
    reference = solve_case(case, "C", terminal_rule=terminal_rule)
    if reference.status != "optimal":
        raise ValueError("CBAM reference is not optimal; target matching is undefined")
    target = sum(row["total"] for row in reference.emissions.values())
    rows = []
    for kappa in sorted(set(float(value) for value in grid)):
        sol = solve_case(case, "G", kappa_override=kappa, terminal_rule=terminal_rule)
        row = dict(kappa=kappa, status=sol.status, cumulative_emissions_MtCO2="", delta_vs_C="",
                   resource_cost_proxy_PV_Meur="", emissions_target_met="", cumulative_EU_imports_Mt="")
        if sol.status == "optimal":
            emissions = sum(part["total"] for part in sol.emissions.values())
            row.update(cumulative_emissions_MtCO2=emissions, delta_vs_C=emissions-target,
                       resource_cost_proxy_PV_Meur=sol.costs["resource_cost_proxy"], emissions_target_met=bool(emissions <= target+1e-7),
                       cumulative_EU_imports_Mt=sum(value for (rid,d,t),value in sol.x.items()
                           if d == case.eu_market and case.recipes[(rid,t)]["steel_node"] != case.eu_market))
        rows.append(row)
    feasible = [row for row in rows if row["emissions_target_met"] is True]
    _write(output / "kappa_grid.csv", list(rows[0]), rows)
    return dict(reference_emissions_MtCO2=target, examined_grid=[row["kappa"] for row in rows],
                smallest_examined_qualifying_kappa=min((row["kappa"] for row in feasible),default=None),
                lowest_cost_examined_qualifying_kappa=min(feasible,key=lambda row:row["resource_cost_proxy_PV_Meur"])["kappa"] if feasible else None,
                claim="grid search only; no monotonicity assumption or global-optimum claim")
