"""Independent hand accounting from solution activities and original CSV rows."""
from collections import defaultdict


def audit_solution(case, sol, tolerance=1e-6):
    if sol.status != "optimal":
        return dict(passed=False,status=sol.status,physical_checks_performed=False)
    balances=defaultdict(float)
    capacity_use=defaultdict(float)
    for (rid,t),amount in sol.q.items():
        for row in case.requirements[(rid,t)]:
            kind=row["requirement_type"]
            coef=float(row["coefficient"])
            if kind in ("input","output"):
                key=(row["input_node"],row["input_id"],t)
                balances[key] += amount*coef*(1 if kind=="output" else -1)
            elif kind=="capacity_pool":
                capacity_use[(row["input_id"],t)] += amount*coef
    for (node,res,block,t),amount in sol.u.items():
        balances[(node,res,t)] += amount
    max_material=max((abs(x) for x in balances.values()),default=0.)
    max_demand=0.
    for (region,grade,demand_case,t),value in case.demand.items():
        delivered=sum(amount for (rid,d,year),amount in sol.x.items() if d==region and year==t and case.recipes[(rid,t)]["grade_id"]==grade)
        max_demand=max(max_demand,abs(delivered-value/1e6))
    max_dispatch=0.
    for (rid,t),amount in sol.q.items():
        if case.recipes[(rid,t)]["grade_id"].strip():
            max_dispatch=max(max_dispatch,abs(amount-sum(v for (r,d,year),v in sol.x.items() if r==rid and year==t)))
    capacity=defaultdict(float)
    for row in case.assets:
        start=int(row["start_year"])
        end=int(row["retirement_year"]) if row.get("retirement_year","").strip() else start+int(row["design_life_years"])
        if row["status"] in ("retired","mothballed","cancelled"):
            continue
        for t in case.years:
            if start<=t<end:
                capacity[(row["pool_id"],t)] += float(row["capacity_tpy"])*float(row["max_utilization"])*float(row.get("scope_share") or 1.)/1e6
    for (oid,vy),amount in sol.inv.items():
        row=case.investments[(oid,vy)]
        start=vy+int(row["lead_years"])
        for t in case.years:
            if start<=t<start+int(row["life_years"]):
                capacity[(f"{row['node_id']}_{row['target_process']}",t)] += amount*float(row["max_utilization"])
    max_capacity=max((max(0.,value-capacity[key]) for key,value in capacity_use.items()),default=0.)
    rate=float(case.param("COMMON","discount_rate"))
    pv=sum((1+rate)**(-(t-case.years[0]))*sum(costs.values()) for t,costs in sol.annual_costs.items())
    objective_error=abs(pv-sol.objective_meur)
    max_complementarity=max((abs(sol.price.get(t,0.)*(sol.S[t]-sol.kappa*sol.L[t])) for t in case.years),default=0.)
    max_clearing_shortfall=max((max(0.,sol.kappa*sol.L[t]-sol.S[t]) for t in case.years),default=0.)
    measures=dict(max_material_residual_Mt=max_material,max_demand_residual_Mt=max_demand,
                  max_dispatch_residual_Mt=max_dispatch,max_capacity_excess_Mt=max_capacity,
                  objective_cashflow_PV_error_Meur=objective_error,
                  max_clearing_shortfall_MtCO2=max_clearing_shortfall,
                  max_certificate_transfer_discrepancy_Meur=max_complementarity)
    passed=all(value<=tolerance for key,value in measures.items() if key!="objective_cashflow_PV_error_Meur") and objective_error<=max(1e-5,abs(pv)*1e-9)
    return dict(passed=bool(passed),status=sol.status,tolerance_Mt=tolerance,**{k:float(v) for k,v in measures.items()})
