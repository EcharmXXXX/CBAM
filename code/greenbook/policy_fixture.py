"""Explicitly synthetic inputs for the three-tier, fixed-recipe policy demonstration."""
import csv


SOURCE = "SYNTHETIC_BENCHMARK_POLICY"


def extend_fixture(directory, start, end):
    names = ("11_recipes", "12_recipe_requirements", "13_resource_supply", "16_transport",
             "17_border_rules", "18_border_flow_map", "19_emission_components", "25_scenario_parameters")
    tables = {}
    for name in names:
        with (directory / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            tables[name] = (reader.fieldnames, list(reader))

    recipes, inputs, supply, transport, border, border_map, emissions, params = (
        tables[name][1] for name in names
    )
    h2_templates = {}
    for row in recipes:
        node, year, rid = row["steel_node"], row["year"], row["recipe_id"]
        if row["iron_tech"] == "BF":
            # Johnson's 12.5% reference, with explicitly synthetic emission adjustments.
            row["scrap_charge_share"] = 0.125
            requirements = [entry for entry in inputs if (entry["recipe_id"], entry["year"]) == (rid, year)]
            ore = next(entry for entry in requirements if entry["input_id"] == "pellets")
            ore.update(input_id="iron_ore", coefficient=1.485)
            inputs.append(dict(ore, input_id="scrap", coefficient=0.1344))
            for entry in emissions:
                if (entry["activity_id"], entry["year"]) == (rid, year):
                    entry["factor"] = float(entry["factor"]) * 0.875
            for entry in border:
                if (entry["crossing_class"], entry["year"]) == ("STEEL_X_BFBOF", year) and node == "X":
                    entry["see_tco2e_per_t_goods"] = float(entry["see_tco2e_per_t_goods"]) * 0.875
        elif row["steel_tech"] == "EAF" and row["iron_tech"] == "":
            row["certificate_class"] = "GREEN_STEEL"
        elif row["iron_tech"] == "H2DRI" and row["grade_id"]:
            h2_templates[rid, year] = row

    for (rid, year), template in h2_templates.items():
        node = template["steel_node"]
        requirements = [entry for entry in inputs if (entry["recipe_id"], entry["year"]) == (rid, year)]
        energy = dict(requirements[0], requirement_type="input", input_id="eaf_power",
                      input_node=node, unit="MWh_per_tcs", destination_node=node, coefficient=0.75)
        inputs.append(energy)
        components = [row for row in emissions if (row["activity_id"], row["year"]) == (rid, year)]
        power_emissions = dict(components[0], component_id="EAF_POWER", boundary_version="POLICY_TOY_EAF_POWER",
                               factor=0.02 * energy["coefficient"], responsibility_included=0,
                               eligibility_included=1, system_included=0, included_elsewhere=1,
                               ets_coverage_share=0)
        emissions.append(power_emissions)
        for suffix, share, eaf_power in (("S125", 0.125, 0.74), ("S25", 0.25, 0.73)):
            new_id = f"{rid}_{suffix}"
            recipes.append(dict(template, recipe_id=new_id, scrap_charge_share=share))
            for row in requirements:
                entry = dict(row, recipe_id=new_id)
                if row["input_id"] in ("pellets", "h2", f"{node}_DRI"):
                    entry["coefficient"] = float(row["coefficient"]) * (1 - share)
                inputs.append(entry)
            # Common metallic recovery is a simplifying fixture assumption.
            inputs.append(dict(energy, recipe_id=new_id, input_id="scrap",
                               unit="t_per_tcs", coefficient=1.05 * share))
            inputs.append(dict(energy, recipe_id=new_id, coefficient=eaf_power))
            # For this fixture only, 70% of the base process factor varies with DRI.
            emissions.extend(dict(row, activity_id=new_id,
                                  factor=float(row["factor"]) * (1 - 0.70 * share)) for row in components)
            emissions.append(dict(power_emissions, activity_id=new_id, factor=0.02 * eaf_power))
            if node == "X":
                crossing = next(row for row in border if (row["crossing_class"], row["year"])
                                == ("STEEL_X_H2DRI", year))
                new_class = f"STEEL_X_H2DRI_{suffix}"
                border.append(dict(crossing, crossing_class=new_class,
                                   see_tco2e_per_t_goods=float(crossing["see_tco2e_per_t_goods"]) * (1 - share)))
                mapping = next(row for row in border_map if (row["crossing_class"], row["year"])
                               == ("STEEL_X_H2DRI", year))
                border_map.append(dict(mapping, crossing_class=new_class, variable_key=f"{new_id}|EU"))

    for node in ("EU", "X"):
        rights = next(row for row in params
                      if row["parameter_id"] == "transferable_share" and row["location_key"] == f"R_{node}_H2DRI")
        for suffix in ("H2DRI_S125", "H2DRI_S25", "SCRAP"):
            params.append(dict(rights, location_key=f"R_{node}_{suffix}"))

    for row in list(supply):
        if row["resource_id"] == "pellets":
            # Split existing mineral supply rather than duplicating a mine's output.
            total = float(row["upper_quantity"])
            row["upper_quantity"] = total * 0.25
            supply.append(dict(row, resource_id="iron_ore", resource_class="iron_ore",
                               upper_quantity=total * 0.75))
        if row["resource_id"] == "h2" and row["block_id"] == "H1":
            supply.append(dict(row, resource_id="eaf_power", resource_class="electricity",
                               quantity_unit="MWh", upper_quantity=50_000_000,
                               private_price_per_unit=45, resource_cost_per_unit=45, emission_factor=0.02,
                               hydrogen_production_method="", renewable_power_evidence_id="",
                               electricity_emissions_tco2e_per_th2=""))

    tables["16_transport"][0].append("market_trade")
    for row in list(transport):
        if row["commodity_class"] != "steel" or row["origin_node"] == row["destination_node"]:
            continue
        for resource, freight, limit in (("scrap", 18, 8_000_000),
                                         ("iron_ore", 14, 70_000_000), ("pellets", 10, 20_000_000)):
            transport.append(dict(row, commodity_class=resource, market_trade="1", loss_fraction=0.01,
                                  private_freight_eur_per_t=freight, resource_freight_eur_per_t=freight,
                                  emissions_tco2e_per_t=0.02, annual_capacity_t=limit))
            # Fixture-only assumption: this pellet class is covered on entry to the EU.
            if resource == "pellets" and row["destination_node"] == "EU":
                year = row["year"]
                template = next(entry for entry in border if
                                (entry["crossing_class"], entry["year"]) == ("HBI_X", year))
                border.append(dict(template, crossing_class="PELLETS_X", cn8="TOY_PELLETS",
                                   see_tco2e_per_t_goods=0.15, secpp_eur_per_t_goods=0.,
                                   replaced_scope="unreplaced"))
                mapping = next(entry for entry in border_map if
                               (entry["crossing_class"], entry["year"]) == ("HBI_X", year))
                border_map.append(dict(mapping, crossing_class="PELLETS_X", variable_kind="f",
                                       variable_key="X|EU|pellets", coefficient=0.99))

    for name, (fields, rows) in tables.items():
        with (directory / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    with (directory / "certificate_strict_benchmarks.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("grade_id", "year", "benchmark_tco2e_per_tcs", "source_id"))
        # A test input for the strict tier, not measured European installation data.
        writer.writerows(("STEEL_AGG", year, 0.20, SOURCE) for year in range(start, end + 1))
