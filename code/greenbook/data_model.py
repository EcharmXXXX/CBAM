"""加载最小实例数据合同为 Case 对象。

只做类型转换和索引，不做任何默认补零；空值保持为 None。
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _num(value):
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).strip())


def _flag(value):
    return str(value).strip() in ("1", "true", "True", "yes")


@dataclass
class Case:
    directory: Path
    years: tuple
    regions: tuple
    eu_market: str
    recipes: dict          # (recipe_id, year) -> row
    steel_recipe_ids: tuple  # 有需求交付的配方（grade 非空）
    requirements: dict      # (recipe_id, year) -> [rows]
    emission_components: dict
    resources: dict         # (node, resource, block, year) -> row
    transport: dict         # (origin, destination, commodity, year) -> row
    border_rules: dict      # (crossing_class, year) -> row
    border_map: dict        # (crossing_class, year) -> [rows]
    certificate_rules: dict  # (rule_case, year) -> row
    demand: dict            # (region, grade, demand_case, year) -> tcs
    assets: list
    investments: dict       # option_id -> row
    scenario_params: dict   # (scenario, parameter, location) -> float 或 str
    recipe_years: dict      # recipe_id -> (years)

    def param(self, scenario, parameter, location="GLOBAL", default=None):
        return self.scenario_params.get((scenario, parameter, location), default)

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        recipe_rows = _rows(directory / "11_recipes.csv")
        recipes = {}
        for row in recipe_rows:
            recipes[(row["recipe_id"], int(row["year"]))] = row
        recipe_years = {}
        for rid, _ in recipes:
            recipe_years.setdefault(rid, tuple(t for (r, t) in recipes if r == rid))
        steel_ids = tuple(sorted({rid for rid in recipe_years
                                  if recipes[(rid, recipe_years[rid][0])]["grade_id"].strip()}))

        requirements = {}
        for row in _rows(directory / "12_recipe_requirements.csv"):
            requirements.setdefault((row["recipe_id"], int(row["year"])), []).append(row)

        components = {}
        for row in _rows(directory / "19_emission_components.csv"):
            if row["activity_kind"] == "recipe":
                components.setdefault((row["activity_id"], int(row["year"])), []).append(row)

        resources = {}
        for row in _rows(directory / "13_resource_supply.csv"):
            resources[(row["node_id"], row["resource_id"], row["block_id"], int(row["year"]))] = row

        transport = {}
        for row in _rows(directory / "16_transport.csv"):
            transport[(row["origin_node"], row["destination_node"], row["commodity_class"],
                       int(row["year"]))] = row

        border_rules = {}
        for row in _rows(directory / "17_border_rules.csv"):
            border_rules[(row["crossing_class"], int(row["year"]))] = row
        border_map = {}
        for row in _rows(directory / "18_border_flow_map.csv"):
            border_map.setdefault((row["crossing_class"], int(row["year"])), []).append(row)

        cert_rules = {}
        for row in _rows(directory / "21_certificate_rules.csv"):
            cert_rules[(row["rule_case"], int(row["year"]))] = row

        demand = {}
        for row in _rows(directory / "07_demand.csv"):
            demand[(row["region_id"], row["grade_id"], row["demand_case"],
                    int(row["year"]))] = _num(row["demand_tcs"])
        regions = tuple(sorted({key[0] for key in demand}))
        years = tuple(sorted({key[3] for key in demand}))

        assets = _rows(directory / "04_assets.csv")
        investments = {}
        for row in _rows(directory / "10_investment_options.csv"):
            investments.setdefault(row["option_id"], row)

        params = {}
        for row in _rows(directory / "25_scenario_parameters.csv"):
            key = (row["scenario_id"], row["parameter_id"], row["location_key"])
            numeric = _num(row["numeric_value"])
            params[key] = numeric if numeric is not None else row["text_value"]

        eu_market = params.get(("COMMON", "eu_market", "GLOBAL"), "")
        return cls(directory=directory, years=years, regions=regions, eu_market=eu_market,
                   recipes=recipes, steel_recipe_ids=steel_ids, requirements=requirements,
                   emission_components=components, resources=resources, transport=transport,
                   border_rules=border_rules, border_map=border_map, certificate_rules=cert_rules,
                   demand=demand, assets=assets, investments=investments,
                   scenario_params=params, recipe_years=recipe_years)
