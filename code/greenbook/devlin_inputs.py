"""Prepare Devlin-informed engineering rows for the common annual steel case."""
from collections import defaultdict
from copy import deepcopy
import json
from math import isclose, isfinite


SOURCE = "Devlin2023:10.1038/s41467-023-38123-2"
EAF_REFERENCE = {0.: (.75, .050), .25: (.73, .0495), .5: (.71, .049)}


def _number(value, label, minimum=0., maximum=None):
    value = float(value)
    if not isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {label}: {value}")
    return value


def _fraction(value, label, *, positive=False):
    result = _number(value, label, maximum=1.)
    if positive and result == 0:
        raise ValueError(f"{label} must be positive")
    return result


def _add_pellets(case, rows):
    seen = set()
    for row in rows:
        node, resource, block, year = (row[key] for key in ("node_id", "resource_id", "block_id", "year"))
        key = (node, resource, block, year)
        if key in seen or year not in case.years or not resource.startswith("pellets_dr_"):
            raise ValueError(f"Invalid or duplicate DR-pellet supply key: {key}")
        seen.add(key)
        fe = _fraction(row["pellet_fe_fraction"], "pellet Fe", positive=True)
        gangue = _fraction(row["pellet_gangue_fraction"], "pellet gangue")
        if fe + gangue > 1:
            raise ValueError(f"Invalid DR-pellet composition: {key}")
        capacity = min(_number(row[field], field) for field in
                       ("ore_limited_t_pellets", "pellet_capacity_t", "delivery_capacity_t"))
        committed = _number(row.get("other_use_t", 0.), "committed pellet uses", maximum=capacity)
        if "private_price_per_t" in row:
            price = _number(row["private_price_per_t"], "pellet price")
        else:
            ore_fe = _fraction(row["mined_ore_fe_fraction"], "mined ore Fe", positive=True)
            # Devlin Eq. 1, in source USD per dry tonne; conversion is analyst-supplied.
            price = (60. + 40. * .62 / ore_fe) * _number(
                row["source_usd_to_model_eur"], "price-year and currency conversion", minimum=1e-12)
            price += _number(row.get("freight_EUR_per_t", 0.), "pellet freight")
        case.resources[key] = dict(
            node_id=node, resource_id=resource, block_id=block, year=year, resource_class="pellets_dr",
            quantity_unit="t", upper_quantity=capacity - committed,
            other_use_deducted=committed, committed_other_use=committed,
            private_price_per_unit=price, resource_cost_per_unit=price,
            emission_factor=_number(row["emission_factor"], "pellet emissions"),
            pellet_fe_fraction=fe, pellet_gangue_fraction=gangue,
            source_id=row["source_id"], cost_quality="engineering", value_status="scenario",
        )


def _replace_requirements(case, key, items):
    old = case.requirements[key]
    recipe_id, year = key
    case.requirements[key] = [r for r in old if r["requirement_type"] not in ("input", "output", "opex")]
    for kind, commodity, node, coefficient, unit, dest in items:
        if coefficient > 0 or kind == "opex":
            case.requirements[key].append(dict(
                recipe_id=recipe_id, year=year, requirement_type=kind, input_id=commodity,
                input_node=node, destination_node=dest, coefficient=coefficient, unit=unit, source_id=SOURCE,
            ))


def _replace_capacity(rows, pool, coefficient):
    matches = [row for row in rows if row["requirement_type"] == "capacity_pool" and row["input_id"] == pool]
    if len(matches) != 1:
        raise ValueError(f"Expected one capacity requirement for {pool}")
    matches[0]["coefficient"] = coefficient


def _add_direct_power(case, rows):
    seen = set()
    for row in rows:
        node, resource, block, year = (row[key] for key in ("node_id", "resource_id", "block_id", "year"))
        key = (node, resource, block, year)
        if key in seen or year not in case.years or not resource:
            raise ValueError(f"Invalid or duplicate direct electricity block: {key}")
        seen.add(key)
        price = _number(row["price_EUR_per_MWh"], "direct electricity price")
        case.resources[key] = dict(
            node_id=node, resource_id=resource, block_id=block, year=year, resource_class="electricity",
            quantity_unit="MWh", upper_quantity=_number(row["annual_MWh"], "direct electricity capacity"),
            private_price_per_unit=price, resource_cost_per_unit=price,
            emission_factor=_number(row["emissions_tco2e_per_MWh"], "direct electricity emissions"),
            source_id=row["source_id"], cost_quality="engineering", value_status="scenario",
        )
    return [case.resources[key] for key in sorted(seen)]


def _process_data(case, settings, iron_node, year):
    dri_fe = _fraction(settings["dri_fe_fraction"], "DRI Fe", positive=True)
    recovery = _fraction(settings["iron_recovery"], "iron recovery", positive=True)
    minimum_fe = _fraction(settings["minimum_pellet_fe_fraction"], "minimum DR pellet Fe", positive=True)
    maximum_gangue = _fraction(settings["maximum_pellet_gangue_fraction"], "maximum DR pellet gangue")
    pellet = settings["pellet_resource_id"]
    blocks = [row for (node, resource, _, t), row in case.resources.items()
              if (node, resource, t) == (iron_node, pellet, year)]
    if not blocks or any(row.get("resource_class") != "pellets_dr" for row in blocks):
        raise ValueError(f"Missing dedicated DR pellet pool: {iron_node}/{pellet}/{year}")
    grades = {_fraction(row["pellet_fe_fraction"], "pellet Fe", positive=True) for row in blocks}
    if len(grades) != 1 or next(iter(grades)) < minimum_fe or any(
        _fraction(row["pellet_gangue_fraction"], "pellet gangue") > maximum_gangue for row in blocks
    ):
        raise ValueError("Split pellet pools by Fe content and enforce recipe impurity limits")
    hydrogen_rate = _number(settings.get("hydrogen_kg_per_tDRI", 48.13), "DRI hydrogen rate", minimum=1e-12)
    compression_share = _fraction(settings.get("compressed_hydrogen_share", 0.), "200 bar compression share")
    heat_efficiency = _fraction(settings["preheat_electric_efficiency"], "preheat efficiency", positive=True)
    direct_power = .06 + 1606.10 / 3600. / heat_efficiency + 2.87 * compression_share * hydrogen_rate / 1000.
    return dri_fe / (next(iter(grades)) * recovery), hydrogen_rate / 1000., direct_power


def _apply_recipe(case, config, hbi_production):
    key = (config["recipe_id"], config["year"])
    recipe = case.recipes[key]
    mode = config["route"]
    node, iron_node = recipe["steel_node"], recipe["iron_node"]
    is_steel = bool(recipe["grade_id"].strip())
    if mode not in ("hot_dri", "cold_dri", "hbi_production", "hbi_eaf") or is_steel != (mode != "hbi_production"):
        raise ValueError(f"Incompatible engineering route for {key}")
    if mode != "hbi_eaf" and (recipe["iron_tech"] != "H2DRI" or node != iron_node):
        raise ValueError(f"Expected a local H2DRI activity for {key}")
    if mode == "hbi_eaf" and recipe["iron_tech"] != "HBI":
        raise ValueError(f"Expected an HBI-EAF activity for {key}")
    if is_steel and recipe["steel_tech"] != "EAF":
        raise ValueError(f"Expected EAF steelmaking for {key}")

    year = key[1]
    product = "tcs" if is_steel else "tdri"
    values = []

    def add(commodity, origin, coefficient, unit="t", destination=node, kind="input"):
        values.append((kind, commodity, origin, coefficient, f"{unit}_per_{product}", destination))

    opex = _number(config["nonresource_opex_EUR_per_t"], "non-resource opex")
    add("nonresource_opex", node, opex, "eur", "", "opex")
    dri_use = 1.
    scrap = 0.
    if is_steel:
        share = _fraction(config["scrap_charge_share"], "metallic scrap share")
        if share not in EAF_REFERENCE and "eaf_MWh_per_tcs" not in config:
            raise ValueError("Provide EAF electricity for a non-Devlin reference scrap share")
        if mode == "hbi_eaf":
            supply_key = (iron_node, config["hbi_resource_id"], year)
            if supply_key not in hbi_production:
                raise ValueError(f"HBI-EAF requires an explicitly prepared HBI source: {supply_key}")
            ore_iron = hbi_production[supply_key]
        else:
            ore_iron = _fraction(config["dri_fe_fraction"], "DRI Fe", positive=True)
        recovered_fe = (1. - share) * ore_iron * _fraction(
            config["eaf_dri_iron_recovery"], "EAF iron recovery from DRI/HBI", positive=True)
        if share:
            recovered_fe += share * _fraction(config["scrap_fe_fraction"], "scrap Fe", positive=True) * _fraction(
                config["eaf_scrap_iron_recovery"], "EAF iron recovery from scrap", positive=True)
        charge = _fraction(config["steel_fe_fraction"], "steel Fe", positive=True) / recovered_fe
        if "metallic_charge_t_per_tcs" in config and not isclose(
            charge, float(config["metallic_charge_t_per_tcs"]), rel_tol=.005
        ):
            raise ValueError(f"Metallic charge fails Fe mass balance for {key}")
        scrap, dri_use = share * charge, (1. - share) * charge
        if dri_use <= 0:
            raise ValueError("A DRI/HBI route must charge some DRI or HBI")
        if scrap:
            add(config["scrap_resource_id"], node, scrap)
        recipe["scrap_charge_share"] = share
    else:
        share = 0.

    power = 0.
    if mode == "hbi_eaf":
        hbi = config["hbi_resource_id"]
        shipped = _hbi_logistics(case, key, config, node, iron_node, dri_use)
        add(hbi, iron_node, shipped)
        power += dri_use * _number(config["reheat_MWh_per_t"], "HBI reheating electricity")
    else:
        pellet_rate, h2_rate, iron_power = _process_data(case, config, iron_node, year)
        add(config["pellet_resource_id"], iron_node, dri_use * pellet_rate)
        add(config["hydrogen_resource_id"], iron_node, dri_use * h2_rate)
        power += dri_use * iron_power
        _replace_capacity(case.requirements[key], config["dri_pool_id"], dri_use)
        if mode == "hbi_production":
            hbi = config["hbi_resource_id"]
            hbi_yield = _fraction(config["briquette_yield"], "briquette yield", positive=True)
            supply_key = (node, hbi, year)
            if not (hbi == "hbi" or hbi.startswith("hbi_")) or supply_key in hbi_production:
                raise ValueError(f"Use a dedicated HBI provenance pool: {supply_key}")
            hbi_production[supply_key] = _fraction(
                config.get("hbi_fe_fraction", config["dri_fe_fraction"]), "HBI Fe", positive=True)
            if hbi_yield * hbi_production[supply_key] > float(config["dri_fe_fraction"]) + 1e-10:
                raise ValueError(f"Briquetting cannot increase total iron mass: {key}")
            add(hbi, node, hbi_yield, "t", "", "output")
            power += .01 * hbi_yield
        if mode == "cold_dri":
            power += dri_use * _number(config["reheat_MWh_per_t"], "cold DRI reheating electricity")

    if is_steel:
        eaf_reference = EAF_REFERENCE.get(share, (None, None))
        power += _number(config.get("eaf_MWh_per_tcs", eaf_reference[0]), "EAF electricity") + .01
        optional = {"lime": eaf_reference[1], "alloys": .011, "electrodes": .002}
        for material, mapping in config.get("consumables", {}).items():
            default = optional[material]
            add(mapping["resource_id"], node, _number(mapping.get("t_per_tcs", default), material))
    add(config["electricity_resource_id"], node, power, "MWh")
    _replace_requirements(case, key, values)
    # Explicitly sign off the separate scope/source assumptions in table 19.
    # H2 electricity remains added by the existing strict GSC tracing logic.
    if not config["emissions_source_id"].strip() or config["emissions_source_id"] not in {
        row.get("source_id") for row in case.emission_components[key]
    }:
        raise ValueError(f"Expected the declared reviewed table 19 source for {key}")
    report = dict(recipe_id=key[0], year=year, route=mode, inputs=case.requirements[key])
    if mode == "hbi_eaf":
        report.update(hbi_shipped_t_per_tcs=shipped,
                      hbi_at_border_t_per_tcs=shipped * float(config["logistics"]["border_fraction"]),
                      hbi_charged_t_per_tcs=dri_use)
    return report


def _hbi_logistics(case, key, config, node, iron_node, charged):
    if node == iron_node:
        raise ValueError("Use the cold DRI recipe for on-site stockpiling")
    hbi = config["hbi_resource_id"]
    logistics = config["logistics"]
    delivered_share = _fraction(logistics["delivered_fraction"], "HBI delivery yield", positive=True)
    at_border_share = _fraction(logistics["border_fraction"], "HBI arrival at border", positive=True)
    if delivered_share > at_border_share:
        raise ValueError("The furnace cannot receive more HBI than arrives at the border")
    shipped = charged / delivered_share
    freight = _number(logistics["freight_EUR_per_t_shipped"], "HBI freight")
    handling = _number(logistics["handling_EUR_per_t_shipped"], "HBI handling")
    arc_key = (iron_node, node, hbi, key[1])
    arc = dict(
        private_freight_eur_per_t=freight + handling,
        annual_capacity_t=_number(logistics["capacity_t_shipped"], "HBI transport capacity"),
        emissions_tco2e_per_t=_number(logistics["emissions_tco2e_per_t_shipped"], "HBI logistics emissions"),
        shared_corridor_id=logistics.get("shared_corridor_id", "").strip(),
    )
    if arc_key in case.transport and any(
        case.transport[arc_key].get(field) != value for field, value in arc.items()
    ):
        # Existing links can be replaced once. Other routes using the link must share its inputs.
        if case.transport[arc_key].get("devlin_configured"):
            raise ValueError(f"Inconsistent HBI route data for {arc_key}")
    case.transport[arc_key] = dict(case.transport.get(arc_key, {}), **arc, devlin_configured=True)
    terms = [row for (_crossing, year), rows in case.border_map.items() if year == key[1]
             for row in rows if row["variable_kind"] == "q" and row["variable_key"] == key[0]]
    if node == case.eu_market:
        if len(terms) != 1:
            raise ValueError(f"Expected one explicit HBI entry mapping for {key}")
        terms[0]["coefficient"] = shipped * at_border_share
    elif terms:
        raise ValueError(f"Unexpected EU border mapping for non-EU HBI destination: {key}")
    return shipped


def update_devlin_inputs(case, path, *, energy_path=None):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    updated = deepcopy(case)
    _add_pellets(updated, config.get("pellet_blocks", ()))
    direct_power = _add_direct_power(updated, config.get("electricity_blocks", ()))
    routes = config.get("recipes", ())
    keys = {(row["recipe_id"], row["year"]) for row in routes}
    if len(keys) != len(routes) or not keys <= updated.recipes.keys():
        raise ValueError("Devlin route rows must match distinct existing table 11 recipe-years")
    hbi_production = {}
    report = [_apply_recipe(updated, row, hbi_production)
              for row in sorted(routes, key=lambda row: row["route"] != "hbi_production")]
    producers = defaultdict(int)
    for (rid, year), rows in updated.requirements.items():
        for row in rows:
            if row["requirement_type"] == "output":
                producers[(row["input_node"], row["input_id"], year)] += 1
    if any(producers[key] != 1 for key in hbi_production):
        raise ValueError("Each prepared HBI pool must have one traceable producer")
    for supply_key in hbi_production:
        for (rid, year), rows in updated.requirements.items():
            if (rid, year) not in keys and any(
                row["requirement_type"] == "input"
                and (row["input_node"], row["input_id"], year) == supply_key for row in rows
            ):
                raise ValueError(f"Prepare all consumers of changed HBI pool {supply_key}")
    energy_report = None
    if energy_path is not None:
        # Electricity and H2 IDs are checked against the prepared recipes.
        from .energy_inputs import update_energy_supply
        energy_report = update_energy_supply(updated, energy_path)
    # Verify physical input/capacity maps before the annual model builds each policy case.
    available = {(node, resource, year) for node, resource, _, year in updated.resources} | producers.keys()
    for row in report:
        for req in row["inputs"]:
            if req["requirement_type"] == "input" and (
                req["input_node"], req["input_id"], row["year"]
            ) not in available:
                raise ValueError(f"Missing physical resource for {row['recipe_id']}: {req['input_id']}")
            if req["requirement_type"] != "input":
                continue
            unit = req["unit"].split("_per_")[0]
            blocks = [block for (node, resource, _, year), block in updated.resources.items()
                      if (node, resource, year) == (req["input_node"], req["input_id"], row["year"])]
            if any(block["quantity_unit"] != unit for block in blocks):
                raise ValueError(f"Resource unit mismatch for {row['recipe_id']}: {req['input_id']}")
    pellets = [updated.resources[(row["node_id"], row["resource_id"], row["block_id"], row["year"])]
               for row in config.get("pellet_blocks", ())]
    return updated, dict(recipes=report, pellet_blocks=pellets, electricity_blocks=direct_power), energy_report
