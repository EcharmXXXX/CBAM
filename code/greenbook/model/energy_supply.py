"""Offline chronological renewable-electricity/hydrogen supply LP (§4).

Time steps form one explicit chronological cycle with hourly delivery rates.
No independent representative-day reset or implicit free initial storage exists.
Capital coefficients are annualised equipment cost plus FOM, supplied by the
caller. Output can feed annual hydrogen supply blocks, including their capacity
and emissions; steel-model electrolysis CAPEX must then not be charged again.
"""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix


@dataclass
class EnergyInput:
    wind_cf: list
    solar_cf: list
    hours: list
    delivery_kg_per_hour: list
    grid_price_EUR_MWh: list
    grid_emissions_kgCO2_MWh: list
    grid_limit_MW: list
    annual_cost: dict  # wind/solar/electrolyser/battery_power per MW; battery_energy per MWh; hydrogen_store per kg
    capacity_limit: dict  # same keys, corresponding physical capacity units
    electrolysis_kg_MWh: float = 20.
    battery_charge_efficiency: float = .95
    battery_discharge_efficiency: float = .95


def solve_energy(data):
    names=("wind","solar","electrolyser","battery_power","battery_energy","hydrogen_store")
    fields=("wind_cf","solar_cf","hours","delivery_kg_per_hour","grid_price_EUR_MWh",
            "grid_emissions_kgCO2_MWh","grid_limit_MW")
    values={name:np.asarray(getattr(data,name),dtype=float) for name in fields}
    n=len(values["hours"])
    if not n or any(a.shape!=(n,) or not np.isfinite(a).all() for a in values.values()):
        raise ValueError("Energy profiles must be finite aligned one-dimensional arrays")
    if any(np.any(values[k]<0) for k in fields) or np.any(values["hours"]<=0):
        raise ValueError("Nonnegative profiles and strictly positive durations are required")
    if np.any(values["wind_cf"]>1) or np.any(values["solar_cf"]>1):
        raise ValueError("Capacity factors must lie in [0,1]")
    if not (0<data.battery_charge_efficiency<=1 and 0<data.battery_discharge_efficiency<=1 and data.electrolysis_kg_MWh>0):
        raise ValueError("Invalid efficiency")
    if any(k not in data.annual_cost or k not in data.capacity_limit for k in names):
        raise ValueError("All equipment costs and capacity limits must be explicit")
    if any(not np.isfinite(data.annual_cost[k]) or data.annual_cost[k]<0 or
           not np.isfinite(data.capacity_limit[k]) or data.capacity_limit[k]<0 for k in names):
        raise ValueError("Equipment costs and limits must be finite and nonnegative")
    durations=values["hours"]
    period_hours=durations.sum()
    # A short test cycle is repeated explicitly for annual cost accounting;
    # a real annual study should use chronological full-year weather data.
    annual_cycles=8760./period_hours
    annual_kg=float(np.dot(values["delivery_kg_per_hour"],durations)*annual_cycles)
    if annual_kg<=0:
        raise ValueError("A positive hydrogen-delivery contract is required")
    cap={key:i for i,key in enumerate(names)}
    ops=("grid","electrolysis","charge","discharge","battery_soc","hydrogen_soc","curtailment")
    offset={key:len(names)+i*n for i,key in enumerate(ops)}
    size=len(names)+len(ops)*n
    cost=np.zeros(size)
    bounds=[(0.,data.capacity_limit[key]) for key in names]+[(0.,None)]*(len(ops)*n)
    for key in names:cost[cap[key]]=data.annual_cost[key]
    for h in range(n):
        cost[offset["grid"]+h]=annual_cycles*durations[h]*values["grid_price_EUR_MWh"][h]
        bounds[offset["grid"]+h]=(0.,values["grid_limit_MW"][h])
    eq=lil_matrix((3*n,size))
    rhs=np.zeros(3*n)
    ub=lil_matrix((5*n,size))
    for h in range(n):
        prev=(h-1)%n
        ix=lambda key:offset[key]+h
        # Power balance in MW, with curtailment explicitly nonnegative.
        eq[h,cap["wind"]]=values["wind_cf"][h]
        eq[h,cap["solar"]]=values["solar_cf"][h]
        for key,coef in (("grid",1),("discharge",1),("charge",-1),("electrolysis",-1),("curtailment",-1)):
            eq[h,ix(key)]=coef
        # Add coefficients rather than assigning: n=1 still has cyclic states.
        eq[n+h,ix("battery_soc")]+=1
        eq[n+h,offset["battery_soc"]+prev]-=1
        eq[n+h,ix("charge")]-=data.battery_charge_efficiency*durations[h]
        eq[n+h,ix("discharge")]+=durations[h]/data.battery_discharge_efficiency
        eq[2*n+h,ix("hydrogen_soc")]+=1
        eq[2*n+h,offset["hydrogen_soc"]+prev]-=1
        eq[2*n+h,ix("electrolysis")]-=data.electrolysis_kg_MWh*durations[h]
        rhs[2*n+h]=-values["delivery_kg_per_hour"][h]*durations[h]
        for j,(op,capacity) in enumerate((("electrolysis","electrolyser"),("charge","battery_power"),
            ("discharge","battery_power"),("battery_soc","battery_energy"),("hydrogen_soc","hydrogen_store"))):
            ub[5*h+j,ix(op)]=1
            ub[5*h+j,cap[capacity]]=-1
    out=linprog(cost,A_eq=eq.tocsr(),b_eq=rhs,A_ub=ub.tocsr(),b_ub=np.zeros(5*n),bounds=bounds,method="highs")
    if not out.success:
        return dict(status="infeasible" if out.status==2 else "failed",message=out.message)
    capacities={key:float(out.x[i]) for key,i in cap.items()}
    operation={key:out.x[index:index+n].tolist() for key,index in offset.items()}
    annual_emissions_kg=float(np.dot(out.x[offset["grid"]:offset["grid"]+n]*durations,
                                            values["grid_emissions_kgCO2_MWh"])*annual_cycles)
    # This footprint currently covers grid operation; embodied equipment and
    # water/compression inputs need explicit additional engineering components.
    return dict(status="optimal",annual_delivered_kg=annual_kg,annual_private_cost_EUR=float(out.fun),
                private_cost_EUR_per_kg=float(out.fun/annual_kg),annual_grid_emissions_kgCO2=annual_emissions_kg,
                operational_grid_emissions_kgCO2_per_kgH2=annual_emissions_kg/annual_kg,
                capacities=capacities,operation=operation,cycle_hours=float(period_hours),
                max_equality_residual=float(np.abs(eq@out.x-rhs).max()),
                boundary="grid operational emissions only; no embodied equipment, water or compression footprint")
