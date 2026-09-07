# Provenance of paired benchmark results

`same_corpus: true` means that both candidates inside one result used the
same corpus. It does not assert that separate result files used the same
corpus revision.

- `opus5_vs_gpt56sol_20260728T035854Z.json` used corpus SHA-256
  `20b2958c3c5b3dcb47648a638ce32494b01b0c01a7b1c75f20330d59b2775c3a`.
  That corpus revision was not preserved, so this result is historical and
  must not be compared longitudinally with the current run.
- `opus5_vs_gpt56sol_20260728T040011Z.json` used corpus SHA-256
  `a8f95862c0fae349c44dbce4977ecde30714adb6984f4efcfc648d09aab3b6e7`,
  which matches the versioned `opus5_vs_gpt56sol_corpus.json`.

Future results embed `corpus_snapshot` and declare
`same_corpus_scope: within_result_candidates` so their exact inputs remain
reproducible even if the canonical corpus later changes.
