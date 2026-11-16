"""Chronological electricity and hydrogen supply LP (§4).

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


EQUIPMENT = ("wind", "solar", "electrolyser", "battery_power", "battery_energy", "hydrogen_store")


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
    direct_load_MW: list | None = None
    wind_emissions_kgCO2_MWh: float = 0.
    solar_emissions_kgCO2_MWh: float = 0.


def solve_energy(data, *, power_mode="mixed"):
    if power_mode not in ("mixed", "islanded", "grid"):
        raise ValueError(f"Unknown power_mode: {power_mode}")
    names=EQUIPMENT
    fields=("wind_cf","solar_cf","hours","delivery_kg_per_hour","grid_price_EUR_MWh",
            "grid_emissions_kgCO2_MWh","grid_limit_MW")
    values={name:np.asarray(getattr(data,name),dtype=float) for name in fields}
    n=len(values["hours"])
    values["direct_load_MW"] = (np.zeros(n) if data.direct_load_MW is None
                                else np.asarray(data.direct_load_MW, dtype=float))
    if not n or any(a.shape!=(n,) or not np.isfinite(a).all() for a in values.values()):
        raise ValueError("Energy profiles must be finite aligned one-dimensional arrays")
    if any(np.any(profile < 0) for profile in values.values()) or np.any(values["hours"]<=0):
        raise ValueError("Nonnegative profiles and strictly positive durations are required")
    if np.any(values["wind_cf"]>1) or np.any(values["solar_cf"]>1):
        raise ValueError("Capacity factors must lie in [0,1]")
    if not (0<data.battery_charge_efficiency<=1 and 0<data.battery_discharge_efficiency<=1
            and np.isfinite(data.electrolysis_kg_MWh) and data.electrolysis_kg_MWh>0):
        raise ValueError("Invalid efficiency")
    renewable_factors = {"wind": data.wind_emissions_kgCO2_MWh, "solar": data.solar_emissions_kgCO2_MWh}
    if any(not np.isfinite(value) or value < 0 for value in renewable_factors.values()):
        raise ValueError("Renewable electricity emission factors must be finite and nonnegative")
    if power_mode == "grid" and any(
        np.any(values[key] != values[key][0]) for key in ("delivery_kg_per_hour", "direct_load_MW")
    ):
        raise ValueError("The grid benchmark requires constant hydrogen delivery and direct load")
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
    bounds=[(0., 0. if power_mode == "grid" and key != "electrolyser"
             else data.capacity_limit[key]) for key in names]+[(0.,None)]*(len(ops)*n)
    for key in names:cost[cap[key]]=data.annual_cost[key]
    for h in range(n):
        cost[offset["grid"]+h]=annual_cycles*durations[h]*values["grid_price_EUR_MWh"][h]
        bounds[offset["grid"]+h]=(0., 0. if power_mode == "islanded" else values["grid_limit_MW"][h])
        if power_mode == "grid":
            bounds[offset["curtailment"]+h] = (0., 0.)
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
        rhs[h] = values["direct_load_MW"][h]
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
    annual_hours = durations * annual_cycles
    grid_mwh = np.asarray(operation["grid"]) * annual_hours
    electrolysis_mwh = float(np.dot(operation["electrolysis"], annual_hours))
    direct_mwh = float(np.dot(values["direct_load_MW"], annual_hours))
    consumed_mwh = electrolysis_mwh + direct_mwh
    grid_emissions_kg = float(grid_mwh @ values["grid_emissions_kgCO2_MWh"])
    renewable_emissions_kg = sum(
        capacities[key] * float(annual_hours @ values[f"{key}_cf"]) * factor
        for key, factor in renewable_factors.items()
    )
    grid_cost = float(grid_mwh @ values["grid_price_EUR_MWh"])
    equipment_cost = {key: capacities[key] * data.annual_cost[key] for key in names}
    power_cost = grid_cost + sum(equipment_cost[key] for key in ("wind", "solar", "battery_power", "battery_energy"))
    power_price = power_cost / consumed_mwh
    power_emission_factor = (grid_emissions_kg + renewable_emissions_kg) / consumed_mwh
    if power_mode == "grid":
        # Separate the two end uses at their actual hourly grid costs and emissions.
        electrolysis = np.asarray(operation["electrolysis"]) * annual_hours
        hydrogen_power_cost = float(electrolysis @ values["grid_price_EUR_MWh"])
        hydrogen_power_emissions = float(electrolysis @ values["grid_emissions_kgCO2_MWh"])
        hydrogen_grid_emissions = hydrogen_power_emissions
    else:
        # Allocate shared generation, curtailment and storage losses by delivered MWh.
        hydrogen_power_cost = power_price * electrolysis_mwh
        hydrogen_power_emissions = power_emission_factor * electrolysis_mwh
        hydrogen_grid_emissions = grid_emissions_kg * electrolysis_mwh / consumed_mwh
    hydrogen_cost = hydrogen_power_cost + equipment_cost["electrolyser"] + equipment_cost["hydrogen_store"]
    return dict(
        status="optimal", power_mode=power_mode, annual_delivered_kg=annual_kg,
        annual_private_cost_EUR=float(out.fun), private_cost_EUR_per_kg=hydrogen_cost / annual_kg,
        annual_equipment_cost_EUR=equipment_cost, annual_grid_purchase_cost_EUR=grid_cost,
        annual_grid_purchase_MWh=float(grid_mwh.sum()), annual_electrolysis_MWh=electrolysis_mwh,
        annual_direct_electricity_MWh=direct_mwh, electricity_cost_EUR_MWh=power_price,
        electricity_emissions_kgCO2e_MWh=power_emission_factor,
        hydrogen_electricity_emissions_kgCO2e_per_kgH2=hydrogen_power_emissions / annual_kg,
        annual_grid_emissions_kgCO2=grid_emissions_kg,
        operational_grid_emissions_kgCO2_per_kgH2=hydrogen_grid_emissions / annual_kg,
        capacities=capacities, operation=operation, cycle_hours=float(period_hours),
        max_equality_residual=float(np.abs(eq@out.x-rhs).max()),
        boundary="Grid operating emissions and supplied wind/solar generation factors; "
                 "equipment, water and compression require separate factors.",
    )
