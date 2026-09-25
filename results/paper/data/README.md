# Paper experiment data

This directory is the paper-facing result layer for PrivacyTrace. Rebuild the
CSV files, JSON manifest, and figures from the repository root with:

```bash
python3 scripts/build_paper_results.py
```

`experimental_summary.json` is the compact machine-readable manifest. The CSV
files split the results by analysis:

- `main_results.csv`: primary arms and aggregation views
- `attribute_results.csv`: all 17 target attributes
- `channel_results.csv` and `channel_effects.csv`: trace-channel ablation
- `domain_results.csv` and `scope_effects.csv`: domain and visibility effects
- `server_results.csv`: all 25 single-server views
- `call_count_results.csv`: visible-call strata lift
- `call_count_absolute_asr.csv`: visible-call strata with absolute trace ASR and paired schema ASR
- `model_generalization.csv`: four attack-model replications
- `representation_results.csv`: raw trace versus lossless catalog
- `two_stage_results.csv`: staged inference and conditional completion
- `dataset_scale.csv`: profile, task, trace, tool, and server counts

The final paper numbers use the post-hoc Unicode-name audit. The build script
checks the frozen summaries, reproduces the narrow correction at server level,
and applies the same rule to every model-generalization run. No attack output or
semantic judgment is regenerated during this correction.
