# CBAM and green-steel certificate model

Research code for comparing CBAM and book-and-claim certificate mechanisms.

## Current scope

- [`code/greenbook`](code/greenbook/README.md) runs the dynamic 2026–2040 model with fixed BF–BOF, hydrogen DRI–EAF and scrap EAF recipes, and endogenous steel, scrap and iron feedstock trade. Its two-region demo uses synthetic inputs.
- The default policy comparison runs N, CBAM (C) and three export-side certificate scenarios. Benchmarks vary by tier, product and year. In each certificate scenario, exporters retire one certificate unit for each tonne of net border liability.
- `code/replication_johnson2026_2040`: calendar, source-data preparation and readiness checks for a prospective 2026–2040 case. The three-year investment lead requires parameter coverage through 2043. The observed 2026 initial state is not yet calibrated; this is not a solved 2026–2040 policy model.
- `code/output`: input contracts, downloaded CSV evidence, provenance and readiness reports.
- `code/review_artifacts`: code-review findings and diagnostic evidence. Findings have not been silently corrected as part of this upload.
- `code/results/stage_a_minimal`: previously generated toy-instance outputs.

## Run

Use Python 3.11 or newer, then install `requirements.txt`. From `code/`:

```sh
python -m greenbook.run_model --synthetic-demo --output results/fixed_recipe_certificate_demo
python -m greenbook.run_minimal
python -m unittest discover -s greenbook/tests -v
python -m replication_johnson2026_2040.check_readiness
```

The readiness command is expected to reject missing calibration inputs.

The [model specification](详细模型建模方案与数据需求.md) defines the eligibility gate, certificate benchmark, ownership and exporter settlement. Use `--include-hbi-trade` for the common HBI trade sensitivity.

## Sources and interpretation

GEM input sources and release dates are recorded in the source register and download manifest. September 2026 capacity snapshots are not automatically January 2026 initial states. EAF capacity is not synonymous with scrap-EAF capacity, and DRI capacity must not be added to crude-steel capacity.

The code and outputs are research work in progress. Passing toy structural tests does not validate all economic assumptions or establish real policy effects. Third-party material retains its source attribution and terms.

