# FitCheck recommender audit

The serving model remains the exchange-label model. Its October result is 80.253% within acceptable over 2,841 cases and 48.205% over 390 exchange cases. Both targets are missed. The early-September winner regressed in October; the release check retained the incumbent. The other variants remain experiment results.

## Data boundaries

- Brand/category offsets, customer fit signals and all fitting observations came from 45,610 January–September orders. The 390 pre-October orders whose return request arrived in October were excluded from training. The final recorded outcome proxy arrived on September 30 at 23:41:18, in workbook time.
- July taught the boosted ordinal correction with histories cut before July. August supplied observed acceptances and size-related rejects with histories cut before August. Early September selected variants on 1,839 known kept or exchanged-to sizes, including 167 exchanges. Late September calibrated confidence on 1,778 separate orders.
- The October workbook opens after candidate selection and calibration. Its acceptable-size sets score all frozen variants. The release check compares only the frozen September winner with the incumbent. Results do not select a second challenger.
- Profile measurement and usual-size capture timestamps are unavailable. The Jan–Sep profile snapshot was used, with future signups excluded at each stage. Return-request time is the available outcome timestamp. Exchange completion time is unavailable. The tests inject October order and outcome timestamps and assert that fitting rejects both.

## Variant comparison

September records a single known accepted size per order. Its exact recorded match is a proxy for within-acceptable accuracy. October has explicit acceptable-size sets.

| Variant | September known-size match | September exchange | October within acceptable | October exchange | Decision |
|---|---:|---:|---:|---:|---|
| First purchase as training label on exchanges, control | 60.468% | 40.719% | 76.874% | 41.282% | Control |
| A. Use exchanged-to size, incumbent | 60.903% | 50.898% | 80.253% | 48.205% | Serves |
| B. Add shrunk signed exchange direction per brand and category | 57.368% | 57.485% | 80.711% | 59.487% | Rejected on September overall |
| C. Gradient-boosted ordinal correction, added to A | 65.307% | 43.713% | 79.549% | 38.462% | Won September overall, failed October release check |
| D. Rank available sizes by predicted acceptance, added to A + C | 45.133% | 41.317% | 64.977% | 46.154% | Rejected on September overall |
| Shopper usual size | — | — | 75.519% of 1,830 | 14.567% of 254 | Subset with usual size recorded |
| Shopper first choice | — | — | 86.202% of 2,841 | 0% of 390 | Observed baseline |

On the usual-size subset, A scores 83.224% overall and 49.606% on exchange cases. Its October exact-match accuracy is 62.865% overall and 40.769% on exchange cases. First choice looks strong overall because the test includes only outcomes with a known kept or exchanged-to size. On exchange cases it fails by construction.

## Misses on October test cases

Distance is the signed number of size steps to the nearest acceptable choice. For jeans, one step is two nominal waist inches. No history means zero kept or exchanged outcomes for that customer's size system. It can include a recorded usual size or other eligible evidence.

| Cohort or slice | Misses / cases | Too small | Too large | Mean absolute steps on misses |
|---|---:|---:|---:|---:|
| Exchange, all | 202 / 390 | 72 | 130 | 1.158 |
| Exchange, Northbound | 21 / 28 | 2 | 19 | 1.286 |
| Exchange, Mile Ten | 13 / 19 | 0 | 13 | 1.308 |
| Exchange, Women's kurtas | 48 / 95 | 22 | 26 | 1.125 |
| Exchange, Men's shirts | 33 / 58 | 12 | 21 | 1.121 |
| Exchange, Women's jeans | 29 / 36 | 4 | 25 | 1.448 |
| No history, all | 186 / 536 | 72 | 114 | 1.253 |
| No history, Northbound | 23 / 45 | 3 | 20 | 1.391 |
| No history, Bluebeam | 19 / 37 | 4 | 15 | 1.000 |
| No history, Denimore | 18 / 39 | 12 | 6 | 1.611 |
| No history, Women's jeans | 60 / 125 | 21 | 39 | 1.417 |
| No history, Men's jeans | 35 / 94 | 14 | 21 | 1.229 |

Of the 202 exchange misses, 173 are one step, 26 are two steps and three are three steps. Of the 186 no-history misses, 145 are one step, 35 are two steps and six are three steps. All brands, categories and signed counts are in [the generated report](results/metrics.json).

## Serving latency

Measured on one Windows machine against warm Uvicorn over localhost and a reused HTTP connection. Each path had 100 requests. End-to-end includes the local HTTP round trip and JSON decoding. Server compute covers the recommendation handler, including response construction. The public Render latency for this change is unmeasured.

| Lookup path | Server median / p95 | Local end-to-end median / p95 |
|---|---:|---:|
| Test ID, precomputed | 0.007 / 0.012 ms | 1.048 / 1.353 ms |
| Customer and SKU, precomputed for benchmark pairs | 0.016 / 0.019 ms | 1.791 / 2.043 ms |
| Manual recommendation | 0.100 / 0.120 ms | 1.662 / 2.046 ms |

## Unreliable charts

The existing rule supports seven flags among women's kurta brands. Saanjh, the strongest unflagged chart by absolute gap, averaged +0.48 size steps across 27 consented kept orders. Its too-small minus too-large return gap was −9.40 percentage points. The rule requires at least 15 kept observations and either an absolute gap of 0.55 steps or a gap of at least 0.20 steps that conflicts with a return imbalance of at least 12 points. Saanjh falls short on both paths. The checks provide no support for an eighth flag under this rule.
