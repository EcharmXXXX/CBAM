"""Screen sites with Devlin's published rankings or an engineering-cost surrogate."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np


FEATURES = ("installation_year",) + tuple(
    f"{resource}_{period}_{stat}"
    for resource in ("wind", "solar")
    for period in ("hourly", "monthly")
    for stat in ("mean", "median", "cv")
)


def _matrix(rows, fields):
    values = np.asarray([[float(row[field]) for field in fields] for row in rows])
    if not len(values) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Screening data must be nonempty, finite and nonnegative")
    return values


def screen(training, candidates):
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error
    from sklearn.model_selection import GroupKFold, GridSearchCV
    from sklearn.multioutput import MultiOutputRegressor

    x = _matrix(training, FEATURES)
    y = _matrix(training, ("energy_EUR_per_MWh", "lcos_ex_ore_labour_EUR_per_tcs"))
    groups = np.asarray([row["country"].strip() for row in training])
    count = len(set(groups))
    if count < 3 or "" in groups:
        raise ValueError("Training requires at least three labelled countries")
    model = MultiOutputRegressor(GradientBoostingRegressor(random_state=0))
    # Each scoring fold excludes an entire country. The inner search tunes on
    # the remaining countries with the same grouped split.
    outer = GroupKFold(n_splits=count)
    inner = GroupKFold(n_splits=min(4, count - 1))
    parameters = {"estimator__max_depth": [2, 3], "estimator__n_estimators": [100, 300],
                  "estimator__learning_rate": [.05, .1]}

    def fit(rows, labels, countries):
        searches = list(inner.split(rows, labels, countries))
        return GridSearchCV(model, parameters, cv=searches, scoring="neg_mean_absolute_error").fit(rows, labels)

    predictions = np.empty_like(y)
    for train, test in outer.split(x, y, groups):
        predictions[test] = fit(x[train], y[train], groups[train]).predict(x[test])
    errors = mean_absolute_error(y, predictions, multioutput="raw_values").tolist()
    points = _matrix(candidates, FEATURES)
    addons = _matrix(candidates, ("ore_EUR_per_tcs", "labour_EUR_per_tcs"))
    fitted = fit(x, y, groups)
    estimated = fitted.predict(points)
    inside = ((points >= x.min(0)) & (points <= x.max(0))).all(1)
    output = []
    for row, (energy, lcos), extra, is_inside in zip(candidates, estimated, addons, inside):
        output.append(dict(site_id=row["site_id"], country=row["country"], installation_year=int(row["installation_year"]),
                           energy_EUR_per_MWh=float(energy), lcos_EUR_per_tcs=float(lcos + extra.sum()),
                           within_training_feature_ranges=bool(is_inside), use="screening_only"))
    return sorted(output, key=lambda row: row["lcos_EUR_per_tcs"]), dict(
        held_out_countries=count, energy_MAE_EUR_per_MWh=errors[0], lcos_ex_ore_labour_MAE_EUR_per_tcs=errors[1],
        best_parameters=fitted.best_params_,
    )


def published_sites(path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    rows = []
    try:
        for site, weather_year, country, lat, lon, installation, lcos, *_ in workbook["Fig 5"].values:
            if type(site) is int:
                rows.append(dict(site_id=site, country=country, latitude=lat, longitude=lon,
                                 weather_year=weather_year, installation_year=installation,
                                 source_LCOS_USD_per_t_semifinished_steel=lcos, use="screening_only"))
    finally:
        workbook.close()
    if not rows:
        raise ValueError("No site rows found in Devlin Source Data Fig 5")
    return sorted(rows, key=lambda row: row["source_LCOS_USD_per_t_semifinished_steel"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--published-xlsx", type=Path)
    source.add_argument("--training", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.published_xlsx:
        if args.candidates:
            parser.error("--candidates is used with --training")
        rows = published_sites(args.published_xlsx)
    else:
        if not args.candidates:
            parser.error("--training requires --candidates")
        with args.training.open(encoding="utf-8-sig", newline="") as training:
            with args.candidates.open(encoding="utf-8-sig", newline="") as candidates:
                rows, metrics = screen(list(csv.DictReader(training)), list(csv.DictReader(candidates)))
        args.output.with_suffix(".validation.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with args.output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
