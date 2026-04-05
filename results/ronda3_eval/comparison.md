# Model Comparison — MedGemma Ronda 3

## Global Metrics

| Metric | base | seal_v1 |
| ------ | ------ | ------- |
| BERTScore F1 | 0.8460 | 0.8560 (+0.0100) |
| Keyword Score | 0.307 | 0.243 (-0.064) |
| Accepted | 125 | 121 (-4) |
| Refusals | 0 | 0 (+0) |
| Avg Speed (t/s) | 1.6 | 1.5 (-0.1) |

## By Language

### ES

| Metric | base | seal_v1 |
| ------ | ------ | ------- |
| BERTScore F1 | 0.8460 | 0.8560 (+0.0100) |
| Keyword Score | 0.307 | 0.243 (-0.064) |

## By Category

### BERTScore F1
| Category | base | seal_v1 |
| -------- | ------ | ------- |
| cardiología | 0.8357 | 0.8416 (+0.0059) |
| cirugía | 0.8481 | 0.8515 (+0.0034) |
| dermatología | 0.8614 | 0.8671 (+0.0057) |
| emergencias | 0.8469 | 0.8495 (+0.0026) |
| endocrinología | 0.8431 | 0.8577 (+0.0146) |
| ginecología | 0.8450 | 0.8669 (+0.0219) |
| hematología | 0.8421 | 0.8581 (+0.0160) |
| infectología | 0.8391 | 0.8468 (+0.0077) |
| medicina_interna | 0.8501 | 0.8575 (+0.0074) |
| nefrología | 0.8480 | 0.8494 (+0.0014) |
| neumología | 0.8498 | 0.8648 (+0.0150) |
| neurología | 0.8451 | 0.8519 (+0.0068) |
| oftalmología | 0.8509 | 0.8490 (-0.0019) |
| oncología | 0.8341 | 0.8439 (+0.0098) |
| pediatría | 0.8496 | 0.8630 (+0.0134) |
| psiquiatría | 0.8376 | 0.8598 (+0.0222) |
| reumatología | 0.8413 | 0.8618 (+0.0205) |
| traumatología | 0.8437 | 0.8460 (+0.0023) |

## Conclusions

- **Best BERTScore F1:** seal_v1
- **Best Keyword Score:** base
- ⚠️ REGRESSION: seal_v1 Keywords -6.4% vs base
- ✅ IMPROVEMENT: seal_v1 BERTScore +0.0100 vs base
