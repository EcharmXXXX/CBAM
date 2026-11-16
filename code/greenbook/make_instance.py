"""写出阶段A最小实例的数据合同CSV。

所有数值为玩具设定（value_status=scenario, source_id=TOY_STAGE_A），
用于结构编码检查，不是采集或校准数据。字段名对齐
output/模型设计与数据清单/实施版数据模板 的表头。
单位：t、tCS、tDRI、tCO2e、2025不变欧元。
"""
import csv
from pathlib import Path

YEARS = (2027, 2028, 2029)
TOY = "TOY_STAGE_A"

RECIPES = {
    # recipe_id: (steel_node, iron_node, iron_tech, steel_tech, grade, scrap_share, cert_class)
    "R_EU_BFBOF":   ("EU", "EU", "BF",    "BOF",      "STEEL_AGG", 0.0, "NONE"),
    "R_EU_SCRAP":   ("EU", "EU", "",      "EAF",      "STEEL_AGG", 1.0, "NONE"),
    "R_EU_H2DRI":   ("EU", "EU", "H2DRI", "EAF",      "STEEL_AGG", 0.0, "GREEN_STEEL"),
    "R_EU_HBIEAF":  ("EU", "X",  "HBI",   "EAF",      "STEEL_AGG", 0.0, "NONE"),
    "R_X_HBIPROD":  ("X",  "X",  "H2DRI", "HBIPROD",  "",     0.0, "NONE"),
    "R_X_BFBOF":    ("X",  "X",  "BF",    "BOF",      "STEEL_AGG", 0.0, "NONE"),
    "R_X_SCRAP":    ("X",  "X",  "",      "EAF",      "STEEL_AGG", 1.0, "NONE"),
    "R_X_H2DRI":    ("X",  "X",  "H2DRI", "EAF",      "STEEL_AGG", 0.0, "GREEN_STEEL"),
}

# (requirement_type, input_id, input_node, coefficient, unit, destination_node)
REQUIREMENTS = {
    "R_EU_BFBOF":  [("opex", "nonresource_opex", "EU", 90.0, "eur_per_tcs", ""),
                    ("input", "pellets", "EU", 1.60, "t_per_tcs", "EU"),
                    ("capacity_pool", "EU_BF", "EU", 1.0, "tcs_per_tcs", "")],
    "R_EU_SCRAP":  [("opex", "nonresource_opex", "EU", 70.0, "eur_per_tcs", ""),
                    ("input", "scrap", "EU", 1.05, "t_per_tcs", "EU"),
                    ("capacity_pool", "EU_EAF", "EU", 1.0, "tcs_per_tcs", "")],
    "R_EU_H2DRI":  [("opex", "nonresource_opex", "EU", 60.0, "eur_per_tcs", ""),
                    ("input", "pellets", "EU", 1.45, "t_per_tcs", "EU"),
                    ("input", "h2", "EU", 0.051, "t_per_tcs", "EU"),
                    ("capacity_pool", "EU_DRI", "EU", 1.05, "tdri_per_tcs", ""),
                    ("capacity_pool", "EU_EAF", "EU", 1.0, "tcs_per_tcs", "")],
    "R_EU_HBIEAF": [("opex", "nonresource_opex", "EU", 65.0, "eur_per_tcs", ""),
                    ("input", "hbi", "X", 1.05, "tdri_per_tcs", "EU"),
                    ("capacity_pool", "EU_EAF", "EU", 1.0, "tcs_per_tcs", "")],
    "R_X_HBIPROD": [("opex", "nonresource_opex", "X", 38.0, "eur_per_tdri", ""),
                    ("input", "pellets", "X", 1.40, "t_per_tdri", "X"),
                    ("input", "h2", "X", 0.051, "t_per_tdri", "X"),
                    ("output", "hbi", "X", 1.0, "tdri_per_tdri", ""),
                    ("capacity_pool", "X_DRI", "X", 1.0, "tdri_per_tdri", "")],
    "R_X_BFBOF":   [("opex", "nonresource_opex", "X", 70.0, "eur_per_tcs", ""),
                    ("input", "pellets", "X", 1.55, "t_per_tcs", "X"),
                    ("capacity_pool", "X_BF", "X", 1.0, "tcs_per_tcs", "")],
    "R_X_SCRAP":   [("opex", "nonresource_opex", "X", 55.0, "eur_per_tcs", ""),
                    ("input", "scrap", "X", 1.05, "t_per_tcs", "X"),
                    ("capacity_pool", "X_EAF", "X", 1.0, "tcs_per_tcs", "")],
    "R_X_H2DRI":   [("opex", "nonresource_opex", "X", 45.0, "eur_per_tcs", ""),
                    ("input", "pellets", "X", 1.40, "t_per_tcs", "X"),
                    ("input", "h2", "X", 0.051, "t_per_tcs", "X"),
                    ("capacity_pool", "X_DRI", "X", 1.05, "tdri_per_tcs", ""),
                    ("capacity_pool", "X_EAF", "X", 1.0, "tcs_per_tcs", "")],
}

# component: (component_id, country, factor, resp, elig, sys, included_elsewhere, ets_share)
EMISSIONS = {
    "R_EU_BFBOF":   [("PROC", "EU", 1.90, 1, 1, 1, 0, 1.0)],
    "R_EU_SCRAP":   [("PROC", "EU", 0.10, 1, 1, 1, 0, 1.0)],
    "R_EU_H2DRI":   [("PROC", "EU", 0.06, 1, 1, 1, 0, 1.0)],
    "R_EU_HBIEAF":  [("PROC_EAF", "EU", 0.08, 1, 1, 1, 0, 1.0),
                     ("PREC_HBI", "X", 0.0735, 1, 1, 0, 1, 0.0)],
    "R_X_HBIPROD":  [("PROC", "X", 0.07, 0, 0, 1, 0, 1.0)],
    "R_X_BFBOF":    [("PROC", "X", 2.00, 1, 1, 1, 0, 1.0)],
    "R_X_SCRAP":    [("PROC", "X", 0.12, 1, 1, 1, 0, 1.0)],
    "R_X_H2DRI":    [("PROC", "X", 0.07, 1, 1, 1, 0, 1.0)],
}

ASSETS = [
    ("A_EU_BF",  "PL_EU", "EU", "EU_BF",  "BF",    "tCS_year", 60_000_000),
    ("A_EU_EAF", "PL_EU", "EU", "EU_EAF", "EAF",   "tCS_year", 25_000_000),
    ("A_X_BF",   "PL_X",  "X",  "X_BF",   "BF",    "tCS_year", 70_000_000),
    ("A_X_EAF",  "PL_X",  "X",  "X_EAF",  "EAF",   "tCS_year", 15_000_000),
    ("A_X_DRI",  "PL_X",  "X",  "X_DRI",  "H2DRI", "tDRI_year", 3_000_000),
]

INVESTMENTS = [
    # option: (node, target_process, capex, fom, lead, life)
    ("INV_EU_DRI", "EU", "DRI", 600.0, 20.0, 2, 20),
    ("INV_EU_EAF", "EU", "EAF", 350.0, 12.0, 2, 25),
    ("INV_X_DRI",  "X",  "DRI", 520.0, 16.0, 2, 20),
]

# (node, resource, block, upper_t, cost_eur_per_t, ef_tco2e_per_t)
RESOURCES = [
    ("EU", "scrap",   "S1", 15_000_000, 180.0, 0.10),
    ("EU", "scrap",   "S2", 15_000_000, 250.0, 0.10),
    ("EU", "pellets", "P1", 60_000_000, 110.0, 0.12),
    ("EU", "pellets", "P2", 100_000_000, 150.0, 0.12),
    ("EU", "h2",      "H1", 2_000_000, 3500.0, 0.50),
    ("EU", "h2",      "H2", 3_000_000, 4500.0, 0.50),
    ("X", "scrap",   "S1", 10_000_000, 150.0, 0.15),
    ("X", "scrap",   "S2", 10_000_000, 230.0, 0.15),
    ("X", "pellets", "P1", 80_000_000, 90.0, 0.15),
    ("X", "pellets", "P2", 120_000_000, 120.0, 0.15),
    ("X", "h2",      "H1", 1_500_000, 3000.0, 0.80),
]

# (origin, destination, commodity, freight_eur_per_t, ef_tco2e_per_t, capacity_t)
TRANSPORT = [
    ("EU", "EU", "steel", 5.0, 0.002, 900_000_000),
    ("X", "X", "steel", 5.0, 0.002, 900_000_000),
    ("EU", "X", "steel", 40.0, 0.05, 20_000_000),
    ("X", "EU", "steel", 40.0, 0.05, 50_000_000),
    ("X", "EU", "hbi", 25.0, 0.03, 8_000_000),
]

# (crossing_class, see, sefa, secpp_eur_per_t, reference_price, pay_price)
BORDER = [
    ("STEEL_X_BFBOF", 2.30, 0.20, 20.0, 80.0, 80.0),
    ("STEEL_X_SCRAP", 0.60, 0.10, 10.0, 80.0, 80.0),
    ("STEEL_X_H2DRI", 0.25, 0.00, 8.0, 80.0, 80.0),
    ("HBI_X", 1.50, 0.00, 15.0, 80.0, 80.0),
]

# (crossing_class, variable_kind, variable_key, coefficient)
BORDER_MAP = [
    ("STEEL_X_BFBOF", "x", "R_X_BFBOF|EU", 1.0),
    ("STEEL_X_SCRAP", "x", "R_X_SCRAP|EU", 1.0),
    ("STEEL_X_H2DRI", "x", "R_X_H2DRI|EU", 1.0),
    ("HBI_X", "q", "R_EU_HBIEAF", 1.05),
]

# (scenario, parameter, location, value, unit)
SCENARIO_PARAMS = [
    ("COMMON", "discount_rate", "GLOBAL", 0.07, "1_per_year"),
    ("COMMON", "origin_carbon_price", "EU", 80.0, "eur_per_tco2e"),
    ("COMMON", "origin_carbon_price", "X", 20.0, "eur_per_tco2e"),
    ("COMMON", "eu_market", "GLOBAL", None, "text:EU"),
    ("COMMON", "transferable_share", "R_EU_H2DRI", 0.8, "share"),
    ("COMMON", "transferable_share", "R_X_H2DRI", 0.6, "share"),
    ("COMMON", "transferable_share", "R_EU_HBIEAF", 0.5, "share"),
    ("COMMON", "build_limit", "INV_EU_DRI", 8_000_000, "tpy"),
    ("COMMON", "build_limit", "INV_EU_EAF", 5_000_000, "tpy"),
    ("COMMON", "build_limit", "INV_X_DRI", 8_000_000, "tpy"),
    ("G1", "kappa", "GLOBAL", 1.0, "share"),
    ("G05", "kappa", "GLOBAL", 0.5, "share"),
]

DEMAND = {"EU": 70_000_000, "X": 40_000_000}


def _write(path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def write_instance(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    rows = []
    for asset_id, plant, node, pool, tech, product, cap in ASSETS:
        rows.append(dict(asset_id=asset_id, plant_id=plant, node_id=node, pool_id=pool,
            process_id=tech.split("_")[0] if tech != "H2DRI" else "DRI", tech_id=tech,
            capacity_product=product, capacity_tpy=cap, max_utilization=0.9,
            design_life_years=40 if "BF" in pool else 30, scope_share=1.0, status="operating",
            start_year=2015, retirement_year="",
            reductant="coal" if tech == "BF" else ("hydrogen" if tech == "H2DRI" else "grid"),
            observed_output_t="", output_year="", asset_vintage_class="pre_2026",
            source_id=TOY, value_status="scenario", notes="阶段A玩具值"))
    _write(directory / "04_assets.csv",
        ["asset_id", "plant_id", "node_id", "pool_id", "process_id", "tech_id", "capacity_product",
         "capacity_tpy", "max_utilization", "design_life_years", "scope_share", "status", "start_year",
         "retirement_year", "reductant", "observed_output_t", "output_year", "asset_vintage_class",
         "source_id", "value_status", "notes"], rows)

    rows = []
    for node in ("EU", "X"):
        for year in YEARS:
            rows.append(dict(region_id=node, grade_id="STEEL_AGG", year=year, demand_case="BASE",
                demand_tcs=DEMAND[node], consumption_definition="apparent_consumption_toy",
                base_price_eur_per_tcs=500.0, price_elasticity="",
                source_id=TOY, value_status="scenario", notes="阶段A玩具需求"))
    _write(directory / "07_demand.csv",
        ["region_id", "grade_id", "year", "demand_case", "demand_tcs", "consumption_definition",
         "base_price_eur_per_tcs", "price_elasticity", "source_id", "value_status", "notes"], rows)

    rows = []
    for option_id, node, process, capex, fom, lead, life in INVESTMENTS:
        rows.append(dict(node_id=node, option_id=option_id, year=2027, target_process=process,
            option_kind="new_build", source_process="", capex_eur_per_tpy=capex,
            fom_eur_per_tpy_year=fom, nonresource_opex_eur_per_t=0.0, lead_years=lead,
            life_years=life, conversion_ratio=1.0, max_utilization=0.9,
            capital_inclusions="full_capex_once_with_terminal_residual",
            payment_schedule_id="FLAT", source_id=TOY, value_status="scenario",
            notes="阶段A玩具投资选项；决策年仅2027，建设期2年"))
    _write(directory / "10_investment_options.csv",
        ["node_id", "option_id", "year", "target_process", "option_kind", "source_process",
         "capex_eur_per_tpy", "fom_eur_per_tpy_year", "nonresource_opex_eur_per_t", "lead_years",
         "life_years", "conversion_ratio", "max_utilization", "capital_inclusions",
         "payment_schedule_id", "source_id", "value_status", "notes"], rows)

    rows = []
    for rid, (steel_node, iron_node, iron_tech, steel_tech, grade, scrap, cert_class) in RECIPES.items():
        for year in YEARS:
            rows.append(dict(recipe_id=rid, year=year, steel_node=steel_node, iron_node=iron_node,
                iron_tech=iron_tech, steel_tech=steel_tech, grade_id=grade,
                scrap_charge_share=scrap, power_class="renew" if "H2DRI" in iron_tech else "grid",
                vintage_class="any", certificate_class=cert_class, goods_mix_id="GM1",
                attribute_path_class="imported_precursor" if iron_tech == "HBI" else "direct",
                source_id=TOY, value_status="scenario", notes="阶段A玩具配方"))
    _write(directory / "11_recipes.csv",
        ["recipe_id", "year", "steel_node", "iron_node", "iron_tech", "steel_tech", "grade_id",
         "scrap_charge_share", "power_class", "vintage_class", "certificate_class", "goods_mix_id",
         "attribute_path_class", "source_id", "value_status", "notes"], rows)

    rows = []
    for rid, reqs in REQUIREMENTS.items():
        for year in YEARS:
            for kind, input_id, node, coef, unit, dest in reqs:
                rows.append(dict(recipe_id=rid, year=year, requirement_type=kind, input_id=input_id,
                    input_node=node, input_class="commodity" if kind in ("input", "output") else "cost_or_capacity",
                    coefficient=coef, unit=unit, destination_node=dest,
                    source_id=TOY, value_status="scenario",
                    notes="HBI由R_X_HBIPROD活动供给并占用X_DRI池" if input_id == "hbi" else ""))
    _write(directory / "12_recipe_requirements.csv",
        ["recipe_id", "year", "requirement_type", "input_id", "input_node", "input_class",
         "coefficient", "unit", "destination_node", "source_id", "value_status", "notes"], rows)

    rows = []
    for node, resource, block, upper, cost, ef in RESOURCES:
        for year in YEARS:
            rows.append(dict(node_id=node, resource_id=resource, resource_class=resource,
                block_id=block, year=year, quantity_unit="t", upper_quantity=upper,
                other_use_deducted=0, committed_other_use=0, resource_cost_per_unit=cost,
                private_price_per_unit=cost, emission_factor=ef, cost_quality="toy",
                source_id=TOY, value_status="scenario", notes="阶段A玩具供给块"))
    _write(directory / "13_resource_supply.csv",
        ["node_id", "resource_id", "resource_class", "block_id", "year", "quantity_unit",
         "upper_quantity", "other_use_deducted", "committed_other_use", "resource_cost_per_unit",
         "private_price_per_unit", "emission_factor", "cost_quality", "source_id",
         "value_status", "notes"], rows)

    rows = []
    for origin, dest, commodity, freight, ef, capacity in TRANSPORT:
        for year in YEARS:
            rows.append(dict(origin_node=origin, destination_node=dest, commodity_class=commodity,
                year=year, mode="sea_rail_toy", distance_km=0, private_freight_eur_per_t=freight,
                resource_freight_eur_per_t=freight, loss_fraction=0.0,
                emissions_tco2e_per_t=ef, annual_capacity_t=capacity, trade_allowed=1,
                source_id=TOY, value_status="scenario", notes="阶段A玩具运输"))
    _write(directory / "16_transport.csv",
        ["origin_node", "destination_node", "commodity_class", "year", "mode", "distance_km",
         "private_freight_eur_per_t", "resource_freight_eur_per_t", "loss_fraction",
         "emissions_tco2e_per_t", "annual_capacity_t", "trade_allowed", "source_id",
         "value_status", "notes"], rows)

    rows = []
    for crossing, see, sefa, secpp, rp, pay in BORDER:
        for year in YEARS:
            rows.append(dict(crossing_class=crossing, year=year, policy_case="CBAM_MAIN",
                cn8="TOY" + crossing[-6:], cn_version="TOY", origin_policy_class="X",
                precursor_class="none", see_tco2e_per_t_goods=see, sefa_tco2e_per_t_goods=sefa,
                secpp_eur_per_t_goods=secpp, reference_price_eur_per_tco2e=rp,
                payment_price_eur_per_tco2e=pay, exposed_share=1.0, replaced_scope="steel_and_hbi",
                tariff_eur_per_t_goods=0.0, legal_version="TOY_STAGE_A",
                emissions_data_basis="toy_default", source_id=TOY, value_status="scenario",
                notes="阶段A玩具边境规则；h=max(0,SEE-SEFA-SECPP/RP)"))
    _write(directory / "17_border_rules.csv",
        ["crossing_class", "year", "policy_case", "cn8", "cn_version", "origin_policy_class",
         "precursor_class", "see_tco2e_per_t_goods", "sefa_tco2e_per_t_goods",
         "secpp_eur_per_t_goods", "reference_price_eur_per_tco2e", "payment_price_eur_per_tco2e",
         "exposed_share", "replaced_scope", "tariff_eur_per_t_goods", "legal_version",
         "emissions_data_basis", "source_id", "value_status", "notes"], rows)

    rows = []
    for crossing, kind, key, coef in BORDER_MAP:
        for year in YEARS:
            rows.append(dict(crossing_class=crossing, year=year, variable_kind=kind,
                variable_key=key, coefficient=coef,
                mapping_basis="goods_share" if kind == "x" else "hbi_input",
                customs_path="final_steel" if kind == "x" else "precursor",
                source_id=TOY, value_status="scenario", notes="一次入境只映射一次"))
    _write(directory / "18_border_flow_map.csv",
        ["crossing_class", "year", "variable_kind", "variable_key", "coefficient", "mapping_basis",
         "customs_path", "source_id", "value_status", "notes"], rows)

    rows = []
    for rid, comps in EMISSIONS.items():
        for year in YEARS:
            for comp_id, country, factor, resp, elig, sys_, elsewhere, ets in comps:
                rows.append(dict(activity_kind="recipe", activity_id=rid, year=year,
                    component_id=comp_id, emission_country=country, gas="CO2", factor=factor,
                    factor_unit="tco2e_per_" + ("tdri" if rid == "R_X_HBIPROD" else "tcs"),
                    gwp_version="AR6", responsibility_included=resp, eligibility_included=elig,
                    system_included=sys_, included_elsewhere=elsewhere, ets_coverage_share=ets,
                    boundary_version="STAGE_A_PROC_ONLY", source_id=TOY, value_status="scenario",
                    notes="阶段A只含工序分量；能源与上游供给排放另计于资源表"))
    _write(directory / "19_emission_components.csv",
        ["activity_kind", "activity_id", "year", "component_id", "emission_country", "gas", "factor",
         "factor_unit", "gwp_version", "responsibility_included", "eligibility_included",
         "system_included", "included_elsewhere", "ets_coverage_share", "boundary_version",
         "source_id", "value_status", "notes"], rows)

    rows = []
    for year in YEARS:
        rows.append(dict(rule_case="MAIN", certificate_class="GREEN_STEEL", year=year,
            t_primary=0.40, t_scrap=0.05, b_primary=2.00, b_scrap=0.20,
            eligibility_boundary="STAGE_A_PROC_ONLY", reference_boundary="STAGE_A_PROC_ONLY",
            allowed_vintage="any", eu_use_rule="exclude_eu_delivery",
            mrv_eur_per_certificate=0.5, banking_allowed=0, cash_exit_allowed=0,
            source_id=TOY, value_status="scenario", notes="研究设定阈值，非行业标准"))
    _write(directory / "21_certificate_rules.csv",
        ["rule_case", "certificate_class", "year", "t_primary", "t_scrap", "b_primary", "b_scrap",
         "eligibility_boundary", "reference_boundary", "allowed_vintage", "eu_use_rule",
         "mrv_eur_per_certificate", "banking_allowed", "cash_exit_allowed", "source_id",
         "value_status", "notes"], rows)

    rows = []
    for scenario, parameter, location, value, unit in SCENARIO_PARAMS:
        rows.append(dict(scenario_id=scenario, parameter_id=parameter, location_key=location,
            year="", numeric_value="" if value is None else value,
            text_value="EU" if value is None else "", unit=unit, rule_version="STAGE_A",
            parent_scenario="", source_id=TOY, value_status="scenario", notes=""))
    _write(directory / "25_scenario_parameters.csv",
        ["scenario_id", "parameter_id", "location_key", "year", "numeric_value", "text_value",
         "unit", "rule_version", "parent_scenario", "source_id", "value_status", "notes"], rows)

    return directory


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "instance"
    write_instance(target)
    print(f"instance written to {target}")
