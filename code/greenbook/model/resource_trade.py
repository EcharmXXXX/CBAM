"""Endogenous shipments of distinct scrap and iron feedstock resource pools."""
from collections import defaultdict
from math import isfinite


def build(case, recipes):
    trades = {}
    arrivals, departures, supplies = defaultdict(list), defaultdict(list), defaultdict(list)
    for node, resource, block, year in case.resources:
        supplies[(node, resource, year)].append((node, resource, block, year))

    for key, row in case.transport.items():
        if str(row.get("market_trade", "")).strip() not in ("1", "true", "True"):
            continue
        origin, dest, resource, year = key
        loss = float(row.get("loss_fraction") or 0)
        cost = float(row["private_freight_eur_per_t"])
        emissions = float(row["emissions_tco2e_per_t"])
        allowed = str(row.get("trade_allowed", "1")).strip()
        if (origin == dest or year not in case.years
                or not resource.startswith(("scrap", "ore", "iron_ore", "pellets"))
                or not 0 <= loss < 1 or not isfinite(cost) or cost < 0
                or not isfinite(emissions) or emissions < 0 or allowed not in ("0", "1")):
            raise ValueError(f"Invalid traded resource arc: {key}")
        source = (origin, resource, year)
        if not supplies[source] or any(case.resources[k]["quantity_unit"] != "t" for k in supplies[source]):
            raise ValueError(f"Resource export needs domestic supply measured in tonnes: {key}")
        target = (dest, resource, year)
        if any(case.resources[k]["quantity_unit"] != "t" for k in supplies[target]):
            raise ValueError(f"Resource import pool must use tonnes: {key}")
        trades[key] = (1 - loss, allowed == "1")
        arrivals[target].append(key)
        departures[source].append(key)

    for (recipe_id, year), recipe in recipes.items():
        for resource, origin, coefficient, dest in recipe.inputs:
            if dest and dest != origin and (origin, dest, resource, year) in trades:
                raise ValueError(f"Recipe {recipe_id} must draw market imports at the destination pool")
    return trades, arrivals, departures, supplies
