"""Freeze product/year certificate benchmarks before solving policy cases."""
from collections import defaultdict
import csv
from math import isfinite

from ..model.build_recipes import build as build_recipes


BENCHMARK_TIERS = ("loose", "normal", "strict")


def read_strict_benchmarks(path):
    """Read comparably measured European low-emissions decile reference inputs."""
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = (row["grade_id"].strip(), int(row["year"]))
            value = float(row["benchmark_tco2e_per_tcs"])
            source = row["source_id"].strip()
            if key in result or not key[0] or not isfinite(value) or value < 0 or not source:
                raise ValueError(f"Invalid or duplicate strict benchmark: {key}")
            result[key] = (value, source)
    return result


def build_benchmarks(case, cbam_solution, strict):
    if cbam_solution.scenario != "C" or cbam_solution.status != "optimal":
        raise ValueError("Certificate benchmarks require a solved CBAM comparison")
    profiles = defaultdict(list)
    recipes = build_recipes(case, qualify=False)
    for (recipe_id, market, year), volume in cbam_solution.x.items():
        if market != case.eu_market or volume <= 0:
            continue
        recipe = recipes[(recipe_id, year)]
        if not isfinite(recipe.eR) or recipe.eR < 0:
            raise ValueError(f"Invalid reference emissions for {recipe_id}/{year}")
        profiles[(recipe.grade, year)].append((recipe.eR, volume))

    result = {}
    product_years = {(recipe.grade, year) for (_, year), recipe in recipes.items() if recipe.is_steel}
    for key in sorted(product_years):
        profile = profiles[key]
        volume = sum(amount for _, amount in profile)
        if volume <= 0:
            raise ValueError(f"No comparable CBAM deliveries to the EU for {key}")
        normal = sum(intensity * amount for intensity, amount in profile) / volume
        remaining = volume / 2
        top_half_emissions = 0.
        for intensity, amount in sorted(profile, reverse=True):
            counted = min(amount, remaining)
            top_half_emissions += intensity * counted
            remaining -= counted
            if remaining <= 0:
                break
        if key not in strict:
            raise ValueError(f"Missing strict benchmark for {key}")
        strict_value, source = strict[key]
        if not isfinite(strict_value) or strict_value < 0 or strict_value > normal or not source:
            raise ValueError(f"Strict benchmark must be sourced and no higher than the normal benchmark: {key}")
        result[key] = dict(loose=top_half_emissions / (volume / 2), normal=normal,
                           strict=strict_value, strict_source=source)
    return result
