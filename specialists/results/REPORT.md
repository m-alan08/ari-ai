| Agent | Model | Metric (test) | Model | Best baseline | Baseline score | Δ | Δ 95% CI | Promoted |
|---|---|---|---|---|---|---|---|---|
| scout | `ari-scout-vulnclass@1.1.0` | macro_f1 | 0.349 | static_analyze_patterns | 0.084 | +0.266 | [+0.252, +0.299] (significant) | yes |
| probe | `ari-probe-strategy@1.1.0` | accuracy | 0.631 | cwe_lookup | 0.606 | +0.025 | [+0.004, +0.048] (significant) | yes |
| trace | `ari-trace-tactics@1.1.0` | f1_at_k | 0.468 | prior_top3 | 0.454 | +0.014 | [-0.030, +0.060] | no |
| diagnose | `ari-diagnose-rootcause@1.1.0` | macro_f1 | 0.601 | cwe_lookup | 0.525 | +0.076 | [+0.025, +0.130] (significant) | yes |
| patch | `ari-patch-strategy@1.1.0` | macro_f1 | 0.654 | cwe_lookup | 0.750 | -0.096 | [-0.121, -0.075] (significant) | no |
| fortify | `ari-fortify-d3fend@1.1.0` | f1_at_k | 0.327 | knn_name | 0.280 | +0.047 | [-0.022, +0.119] | no |
| verify | `ari-verify-evidence@1.1.0` | macro_f1 | 0.971 | diff_rules | 0.864 | +0.107 | [+0.091, +0.124] (significant) | yes |
