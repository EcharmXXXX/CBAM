"""Feed dated engineering supply blocks into the annual steel model."""
import json

from .model.energy_supply import EQUIPMENT, EnergyInput, solve_energy


def _profile(value, count):
    return value if isinstance(value, list) else [value] * count


def _supply_input(config, mode):
    fields = dict(config)
    hours = fields["hours"]
    zeros = ("grid_price_EUR_MWh", "grid_emissions_kgCO2_MWh", "grid_limit_MW") if mode == "islanded" else (
        "wind_cf", "solar_cf"
    )
    for field in zeros:
        fields.setdefault(field, 0.)
    for field in ("wind_cf", "solar_cf", "delivery_kg_per_hour", "grid_price_EUR_MWh",
                  "grid_emissions_kgCO2_MWh", "grid_limit_MW", "direct_load_MW"):
        if field in fields:
            fields[field] = _profile(fields[field], len(hours))
    if mode == "grid":
        for field in ("annual_cost", "capacity_limit"):
            fields[field] = {
                **{equipment: 0. for equipment in EQUIPMENT if equipment != "electrolyser"},
                **fields[field],
            }
    return EnergyInput(**fields)


def update_energy_supply(case, path):
    """Solve the selected project blocks before constructing any policy scenario."""
    projects = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(projects, list) or not projects:
        raise ValueError("Energy supply input must be a nonempty list of project blocks")
    used_inputs = {
        (row["input_node"], row["input_id"], year)
        for (_, year), rows in case.requirements.items()
        for row in rows
        if row["requirement_type"] == "input" and float(row["coefficient"]) > 0
    }
    pools = {}
    for key in case.resources:
        node, resource, _, year = key
        pools.setdefault((node, resource, year), set()).add(key)
    pending = {}
    configured_pools = set()
    diagnostics = []
    for project in projects:
        node, block, year = (project[field] for field in ("node_id", "block_id", "year"))
        h2 = project["resource_id"]
        mode = project["power_mode"]
        source = project["source_id"]
        evidence = project.get("renewable_power_evidence_id", "").strip()
        power = project.get("electricity_resource_id")
        if not all(isinstance(value, str) and value.strip() for value in (node, h2, block, source)):
            raise ValueError("Each project must have a node, hydrogen resource, block, and source ID")
        if type(year) is not int or year not in case.years or mode not in ("islanded", "grid"):
            raise ValueError("Each project requires a model year and islanded or grid power_mode")
        if mode == "grid" and evidence:
            raise ValueError("The industrial grid benchmark cannot supply renewable power evidence")
        data = _supply_input(project["input"], mode)
        if mode == "islanded":
            for technology in ("wind", "solar"):
                factor_field = f"{technology}_emissions_kgCO2_MWh"
                if (data.capacity_limit[technology] > 0 and any(getattr(data, f"{technology}_cf"))
                        and factor_field not in project["input"]):
                    raise ValueError(f"Provide {factor_field} for usable {technology} generation")
        result = solve_energy(data, power_mode=mode)
        if result["status"] != "optimal":
            raise ValueError(f"Energy supply {(node, h2, block, year)}: {result['message']}")
        direct_mwh = result["annual_direct_electricity_MWh"]
        if bool(direct_mwh) != bool(power) or (power is not None and (
            not isinstance(power, str) or not power.strip() or power == h2
        )):
            raise ValueError("A positive direct load requires a separate electricity_resource_id")

        h2_factor = result["hydrogen_electricity_emissions_kgCO2e_per_kgH2"]
        resources = [(h2, "h2", "t", result["annual_delivered_kg"] / 1000.,
                      result["private_cost_EUR_per_kg"] * 1000., h2_factor)]
        if power:
            resources.append((power, "electricity", "MWh", direct_mwh, result["electricity_cost_EUR_MWh"],
                              result["electricity_emissions_kgCO2e_MWh"] / 1000.))
        for resource, kind, unit, quantity, price, factor in resources:
            pool = (node, resource, year)
            key = (node, resource, block, year)
            if pool not in used_inputs:
                raise ValueError(f"Modeled energy resource has no matching input in table 12: {pool}")
            if pool in configured_pools or pools.get(pool, set()) - {key}:
                raise ValueError(f"Use a dedicated resource ID or replace the sole existing block: {pool}")
            configured_pools.add(pool)
            row = dict(node_id=node, resource_id=resource, resource_class=kind, block_id=block,
                       year=year, quantity_unit=unit, upper_quantity=quantity, other_use_deducted=0,
                       committed_other_use=0, resource_cost_per_unit=price, private_price_per_unit=price,
                       emission_factor=factor, cost_quality="engineering", source_id=source,
                       value_status="scenario", notes=f"Energy LP {mode}, dedicated annual supply block")
            if kind == "h2":
                row.update(hydrogen_production_method="water_electrolysis",
                           renewable_power_evidence_id=evidence,
                           electricity_emissions_tco2e_per_th2=h2_factor)
            pending[key] = row
        diagnostics.append(dict(node_id=node, year=year, block_id=block, resource_id=h2,
                                electricity_resource_id=power, source_id=source,
                                renewable_power_evidence_id=evidence,
                                **{key: value for key, value in result.items() if key != "operation"}))
    case.resources.update(pending)
    return diagnostics
