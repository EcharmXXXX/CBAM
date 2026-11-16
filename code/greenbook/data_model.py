"""加载最小实例数据合同为 Case 对象。

只做类型转换和索引，不做任何默认补零；空值保持为 None。
"""
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _num(value):
    if value is None or str(value).strip() == "":
        return None
    result = float(str(value).strip())
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric input")
    return result


def _put(index, key, value):
    if key in index:
        raise ValueError(f"Duplicate input key: {key}")
    index[key] = value


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
    investments: dict       # (option_id, decision_year) -> row
    scenario_params: dict   # (scenario, parameter, location) -> float 或 str
    recipe_years: dict      # recipe_id -> (years)
    attribute_rights: list = field(default_factory=list)
    build_limits: list = field(default_factory=list)

    def param(self, scenario, parameter, location="GLOBAL", default=None, year=None):
        if year is not None and (scenario, parameter, location, year) in self.scenario_params:
            return self.scenario_params[(scenario, parameter, location, year)]
        return self.scenario_params.get((scenario, parameter, location), default)

    @classmethod
    def load(cls, directory, demand_case=None):
        directory = Path(directory)
        recipe_rows = _rows(directory / "11_recipes.csv")
        recipes = {}
        for row in recipe_rows:
            key = (row["recipe_id"], int(row["year"]))
            if key in recipes:
                raise ValueError(f"Duplicate recipe: {key}")
            recipes[key] = row
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
            _put(resources, (row["node_id"], row["resource_id"], row["block_id"], int(row["year"])), row)

        transport = {}
        for row in _rows(directory / "16_transport.csv"):
            _put(transport, (row["origin_node"], row["destination_node"], row["commodity_class"], int(row["year"])), row)

        border_rules = {}
        for row in _rows(directory / "17_border_rules.csv"):
            _put(border_rules, (row["crossing_class"], int(row["year"])), row)
        border_map = {}
        for row in _rows(directory / "18_border_flow_map.csv"):
            border_map.setdefault((row["crossing_class"], int(row["year"])), []).append(row)

        cert_rules = {}
        for row in _rows(directory / "21_certificate_rules.csv"):
            _put(cert_rules, (row["rule_case"], int(row["year"])), row)

        demand = {}
        demand_rows = _rows(directory / "07_demand.csv")
        cases = {row["demand_case"] for row in demand_rows}
        if demand_case is None:
            if len(cases) != 1:
                raise ValueError("Select one demand_case explicitly")
            demand_case = next(iter(cases))
        if demand_case not in cases:
            raise ValueError(f"Unknown demand_case: {demand_case}")
        for row in demand_rows:
            if row["demand_case"] != demand_case:
                continue
            _put(demand, (row["region_id"], row["grade_id"], row["demand_case"], int(row["year"])), _num(row["demand_tcs"]))
        regions = tuple(sorted({key[0] for key in demand}))
        years = tuple(sorted({key[3] for key in demand}))

        assets = _rows(directory / "04_assets.csv")
        investments = {}
        for row in _rows(directory / "10_investment_options.csv"):
            key = (row["option_id"], int(row["year"]))
            if key in investments:
                raise ValueError(f"Duplicate investment batch: {key}")
            if row.get("option_kind", "new_build") != "new_build":
                raise ValueError("Retrofit requires explicit source-vintage conversion constraints")
            investments[key] = row

        params = {}
        for row in _rows(directory / "25_scenario_parameters.csv"):
            key = (row["scenario_id"], row["parameter_id"], row["location_key"])
            if row.get("year", "").strip():
                key += (int(row["year"]),)
            numeric = _num(row["numeric_value"])
            if key in params:
                raise ValueError(f"Duplicate scenario parameter: {key}")
            params[key] = numeric if numeric is not None else row["text_value"]

        eu_market = params.get(("COMMON", "eu_market", "GLOBAL"), "")
        return cls(directory=directory, years=years, regions=regions, eu_market=eu_market,
                   recipes=recipes, steel_recipe_ids=steel_ids, requirements=requirements,
                   emission_components=components, resources=resources, transport=transport,
                   border_rules=border_rules, border_map=border_map, certificate_rules=cert_rules,
                   demand=demand, assets=assets, investments=investments,
                   scenario_params=params, recipe_years=recipe_years,
                   attribute_rights=_rows(directory / "22_attribute_rights.csv") if (directory / "22_attribute_rights.csv").exists() else [],
                   build_limits=_rows(directory / "24_build_limits.csv") if (directory / "24_build_limits.csv").exists() else [])
