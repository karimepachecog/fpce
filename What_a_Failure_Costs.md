# What a Failure Costs: Energy and Water Wasted by Compute That Runs to Its Own Failure

**Subtitle:** Linking doomed-workload detection to the physical cost of inaction.

**Authors:** Karime Pacheco Gallegos, Hannia Ashley Alvarado Galván, Santiago Basaldua Ramírez, Meribeth Yamilet Perez Espinoza

**Code and Data:** Accompanying repository `fpce`. Raw Alibaba traces are acquired from the public Beijing OSS mirror. Google Cluster Data 2019 is a one-cell, one-week export of lifecycle events. Coefficient sources are cited in `params/physical_cost.toml`. Processed tables and checksums are documented with the repository.

---

## Abstract

Warehouse-scale computers expend energy and cooling water on batch instances that will fail, but that physical cost remains invisible under reactive monitoring and static PUE/WUE reporting. We present a Fault-triggered Physical Cost Estimator (FPCE) that scores doomed batch instances at admission and translates remaining runtime into a coefficient-bounded range of IT energy (kilowatt-hours), facility energy (kilowatt-hours), and onsite cooling water (liters). The claim is instance-level doomed-workload detection, not hardware-fault prediction: the Alibaba cluster-trace-v2018 records zero machine-status transitions.

On a time-based split of the primary rack, locked before model selection (3,974,412 completed test instances; 3,778 failures; positive rate 0.0951%), a histogram gradient-boosted classifier attains PR-AUC 0.802 and ROC-AUC 0.984. At threshold 0.9 — selected from the test precision/recall grid, not pre-registered — it anticipates 1,210 of 1,276 failures that have a recorded end after admission (median lead 17 s). Physical costing is restricted to a stricter subset: 204 test failures with measured duration of at least 60 s. Allowing all 204 to run to completion is an **oracle upper bound** of 3.39–9.97 IT kWh (1.52–4.79 L). A kill-at-alert policy attributes 3.32–9.78 IT kWh; a reactive retry-or-runtime baseline attributes 3.29–9.68 IT kWh. The incremental model-minus-baseline gap is 0.034–0.097 IT kWh. Killing the 20,523 test false positives would discard 60–179 IT kWh of useful compute, which exceeds the 204-event waste pool. No public dataset joins failure events to metered facility water; liter figures are coefficient ranges, not measurements.

## 1. Introduction

A batch instance that will fail still occupies a machine until it does. Between admission and that eventual failure, the instance draws IT power, the facility supplies overhead energy, and evaporative or hybrid cooling plants withdraw water. Under current practice that physical cost is not attached to the failure. Operators see resource-level alerts (retry-until-success, a runtime threshold, or a utilization alarm). Sustainability reporting sees static annual ratios — power usage effectiveness (PUE) and water usage effectiveness (WUE) — that do not distinguish a doomed workload from a successful one. The energy burned between a decision that could have been taken at admission and the moment a reactive rule would have acted is discarded work. It is also, at present, uncounted work.

This work asks a narrower question than “optimize a cooling plant” and a more specific one than “detect anomalies.” For a fixed anomaly class (batch-instance failure) and a fixed reactive baseline (a retry-count or runtime-threshold rule), does a predictor that fires at instance admission produce a positive lead time on a measurable share of held-out failed instances, and is the physical cost accumulated during that remaining runtime strictly greater than zero when translated through a published coefficient envelope? The magnitude of any saving is treated as an experimental result, not an a priori percentage. No claim is made that the resulting kilowatt-hour or liter figures are measurements from an operating facility.

An earlier formulation of this problem treated infrastructure and cooling faults as the prediction target. The Alibaba cluster-trace-v2018 does not support that target. The `machine_meta` table records zero status transitions across 4,034 machines (17,587 rows `USING`, 5 `IMPORT_INSTALLING`). There is no hardware-fault or cooling-fault ground truth in this corpus. A machine-minute prediction target with a 30-minute horizon is also not rare: each machine sees on the order of 550 instance failures over eight days, so about 37% of 30-minute windows are positive and a constant “always fail” classifier matches that precision. The appropriate unit is therefore the batch instance. Instance-level `Failed` / `Interrupted` is 0.1679% of completed instances on the primary rack. Terminating a doomed instance at admission is an action production schedulers already take. The claim supported by these traces is doomed-workload detection and the physical cost of allowing those workloads to run. Facility telemetry with maintenance records would be required to reopen the infrastructure-level question (Section 5.4).

This paper makes four contributions.

1. **A leakage-controlled, instance-level prediction table** from Alibaba cluster-trace-v2018, with a time split locked before model selection, a held-out replication rack, an explicit allow/deny feature contract, and a costing-eligibility rule that does not impute a duration when `end_time` is missing.
2. **A cited coefficient registry** in which idle and peak power are a reproducible hardware-matched SPECpower envelope; PUE and site WUE are figure-level citations to a single national report (Shehabi et al., 2024); and water is \(\text{IT kWh} \times \text{WUE}\) with no cooling-share multiplier, because WUE’s denominator is already IT energy (The Green Grid, 2011).
3. **A cross-provider attempt table** from Google Cluster Data 2019, with the unit of analysis (Borg attempt, not Borg instance lifetime), resource-scale alignment (`plan_cpu_frac`), and evaluation metrics chosen so that a ~109× prevalence gap cannot be mistaken for a successful transfer.
4. **An end-to-end evaluation** of an admission-time classifier against a reactive baseline, with remaining runtime translated through a 16-corner Fan / PUE / WUE sweep. Oracle ceilings are labeled as such in the same table cell as the number; ranges remain ranges.

The structural hypothesis holds: the classifier produces a positive lead time on a measurable share of windowed test failures, and the translated cost on those events is strictly greater than zero. The operational finding is narrower. Incremental energy versus the reactive baseline on the 204-event costing sample is 0.034–0.097 IT kWh. False-positive kills, if executed, dominate that sample.

## 2. Related Work

Three bodies of work bound the claims that public traces can support.

**Reactive monitoring and static efficiency ratios.** Production operations rely on threshold tools (Prometheus, Nagios, and related stacks) that fire after a signal crosses a limit. They do not attach a physical cost to the interval between an earlier possible decision and that firing. Parallel to that, operators and auditors report PUE and WUE as annual site ratios. Those ratios are known to extrapolate poorly outside the conditions under which they were derived, and they do not attribute water or energy to a specific failed job. The present work does not replace either practice. It asks whether a predicted instance failure can be given a coefficient-bounded cost that those practices currently leave unassigned.

**Physics-informed digital twins for cooling optimization.** Jadhav and Liu (2026a) developed a Modelica-based digital twin of the liquid cooling plant at the Frontier exascale supercomputer, validated against a full year of ten-minute operational data. Jadhav and Liu (2026b) extended this to co-design across coolant-distribution-unit partitions, reporting approximately 35% annual cooling-energy savings; a subsequent paper extends the framework to full life-cycle optimization (Jadhav and Liu, 2026c). This is the most directly relevant prior art, and it is why the present work does not use the term “digital twin.” Those systems are physics-simulated, facility-validated, and annual-scale. They optimize a cooling plant given proprietary Oak Ridge National Laboratory telemetry that is not available here, and they do not respond to an individual predicted workload failure in real time. Equating a coefficient lookup with a digital twin would overstate fidelity relative to that line of work.

**Failure prediction and water-aware control.** Reviews in *EPJ Special Topics* (2026) and *ScienceDirect* (2026) survey AI and digital-twin approaches to cooling-failure prediction, indicating that general novelty claims in that space require a specific gap. An applied study (IRJMETS, 2025) reports LSTM- and genetic-algorithm-based real-time WUE optimization on two years of operational Tier IV data, reducing WUE from 1.8 to 1.35 L/kWh. That result shows that water-aware control is achievable on facility data. It optimizes continuously rather than attaching a cost estimate to a specific predicted fault, and it uses operational telemetry that is not available in the public traces used here.

**Power models and cluster traces.** Fan, Weber, and Barroso (2007) established the linear utilization-to-power model used here: \(P = P_{\mathrm{idle}} + (P_{\mathrm{peak}} - P_{\mathrm{idle}}) \times u\). SPECpower_ssj2008 supplies the idle and peak envelope, after per-node normalization, for servers in the same socket/thread class as the Alibaba rack. The Alibaba Cluster Trace Program v2018 provides per-instance batch status and machine utilization (Guo et al., 2019; Alibaba, 2018). Google Cluster Data 2019 documents Borg’s instance lifecycle, including eviction and reschedule (Tirmazi et al., 2020). The MIT Supercloud Dataset (Samsi et al., 2021) provides measured GPU power used only as a check on the linear *form*, not as a source of CPU coefficients.

**Gap.** No work identified in this review ties a specific predicted anomaly to a counterfactual physical cost, computed from public rather than facility-proprietary telemetry, and reported with explicit uncertainty rather than as a point estimate. That is the gap addressed here. The claim is narrower than general-purpose digital-twin sustainability optimization, and narrower than hardware-fault detection. Adjacent work on detecting AI training runs from power and network signals (Hardware-Level Governance of AI Compute, 2026) is structurally related and is reserved as future work (Section 5.4).

## 3. Methods

### 3.1 Hypothesis and evaluation protocol

**Hypothesis.** For batch-instance failure, and against a reactive baseline that fires on retry count or on runtime versus a task-level median, a predictor that decides at instance admission (`decision_time = start_time`) will produce a positive lead time on a measurable proportion of held-out failed instances, and the physical cost accumulated during that remaining runtime will be strictly greater than zero on those events when translated through the coefficient envelope in Section 3.7.

No percentage reduction is claimed in advance. The lead-time distribution and the kilowatt-hour and liter ranges are experimental outputs, not inputs to the design.

**Evaluation protocol.** The primary-rack time split is locked before model selection (`split_timestamp = 518{,}355` s). The replication rack (failure domain 52) is unused for training, hyperparameter search, or costing until primary-rack evaluation is complete. Google attempts are a cross-provider evaluation set, not a training pool. A diagnostic transfer experiment on a restricted feature set (Section 4.3) does not replace the twelve-feature classifier in Section 3.6.

### 3.2 Datasets

**Alibaba cluster-trace-v2018 (primary).** Eight-day production trace with machine-level utilization (`machine_usage`) and per-instance batch status (`batch_instance`), including explicit `Failed` labels. Data are acquired from the public Beijing OSS mirror. Training uses a homogeneous 40-machine rack in failure domain 51 (96 hardware threads, `mem_size` 100). Host utilization is resampled to a one-minute grid per rack and joined to instances only at timestamps \(\le\) `decision_time`.

**Alibaba cluster-trace-v2018, held-out rack (replication).** A second homogeneous 40-machine rack from failure domain 52, same eight-day window, same hardware specification. Measured CPU utilization is 40.23% versus 40.72% on the primary rack; instance-level failure rates are 0.1695% versus 0.1679%. This is a replication check across machine sets, not an out-of-distribution test. Genuine shift testing would require a different time window or a different hardware class.

**Google Cluster Data 2019 (cross-provider evaluation).** One cell, first seven days after the 600 s origin, exported as 1,718 parquet shards (3.4 GB, 210,978,166 lifecycle events). The modelling unit is a Borg *attempt* (a SCHEDULE paired with the next terminal event), not a `(collection_id, instance_index)` lifetime: Borg reschedules after EVICT/KILL, and collapsing to one row per instance makes the label an arbitrary choice of first versus last terminal. The 2011 Google trace is unused.

**Coefficient sources (not telemetry).** Fan et al. (2007) for the power-model form. SPECpower_ssj2008 public results for idle/peak envelopes, filtered to two-socket servers with 88–96 hardware threads and divided by `# of Identical Nodes`. Shehabi et al. (2024), LBNL-2001637, Figure 4.6 (PUE) and Figure 4.7 (site WUE). The Green Grid White Paper #35 (2011) for the WUE identity. Operator-published PUE and WUE point values (Google, Microsoft, Meta, Equinix, AWS) are stored as a multiplicative scale comparison against LBNL, not as a substitute for the national-average costing range. MIT Supercloud GPU power traces are used only to fit the linear form on SM utilization versus watts.

No dataset used here contains joint fault-and-water ground truth. No public dataset does. Physical-cost results are therefore ranges bounded by published coefficients, not point measurements.

### 3.3 Prediction unit and labels

The prediction table contains one row per batch instance.

| Outcome | Rule |
|---------|------|
| Positive (`failed=1`) | `batch_instance.status` \(\in\) \{`Failed`, `Interrupted`\} |
| Negative | `Terminated` |
| Censored (excluded from training) | still running: `Running` / `Ready` / `Waiting` |

`Waiting` is not an outcome; Alibaba schema documentation describes it as an instance not yet initialized. Failed rows with `end_time=0` keep `start_time` as a failure proxy for classification but are excluded from costing: there is no measured waste window. When the parent task has a recorded end, those rows carry a parent-task upper bound, flagged as imputed. That upper bound is never mixed into costing-eligible rows.

**Costing eligibility.** An instance is costing-eligible if and only if it failed, the waste window is measured (not imputed), and duration \(\ge\) 60 s. The 60 s floor is a design choice: shorter failures have no useful energy to save at one-minute host resolution, and inventing a duration would contaminate every downstream kilowatt-hour figure.

**Decision time.** The admission decision is taken at instance start (`decision_time = start_time`), before any of the instance’s own telemetry exists. Host context is therefore a trailing window of *machine* utilization ending at `decision_time`, not post-completion instance CPU or memory averages.

**Why not machine-minute.** An auxiliary horizon column remains on the host grid for diagnostics. At 30 minutes its positive rate is 36.84% (primary) / 37.59% (replication). Always-predict-1 precision at that target is ~37%. That column is denied as a training feature. The classifier trains on `failed` among completed (non-censored) instances only.

### 3.4 Corpus construction

Alibaba traces are filtered to two homogeneous 40-machine racks, converted to parquet, and resampled to a one-minute host grid. Instance events join planned resources from the parent task, attach the binary label, and compute measured waste windows. A time-based 75/25 split on `start_time` is locked before model selection. SPECpower public results are parsed, normalized per node, and filtered to the 96-thread envelope; idle and peak bounds in the coefficient file are locked to that extracted table. Supercloud GPU power is fit as a form check and is not copied into the CPU envelope.

Google shards are unordered: every file spans the full `collection_id` range, so shard-at-a-time aggregation would split instances. An out-of-core window pairs each terminal with the preceding SCHEDULE and collapses extra terminals that share a schedule. `attempt_index` is analogous to Alibaba `seq_no`. EVICT and KILL are recorded but excluded from training, so a model trained on Alibaba Failed/Terminated can be scored on Google FAIL versus FINISH rather than on preemption. A stratified FAIL+FINISH sample is the population for the restricted-feature diagnostic in Section 4.3. The twelve-feature classifier of Section 3.6 was not evaluated on Google.

### 3.5 Leakage control

A machine-readable feature contract lists columns allowed at admission and columns denied because they are outcomes, post-completion telemetry, or identity keys.

**Denied (not exhaustive):** instance telemetry known only after completion (`cpu_avg`, `cpu_max`, `mem_avg`, `mem_max`); outcome timestamps and derivatives (`end_time`, waste-window lengths); identity columns that leak across sibling instances of the same failing job (`instance_name`, `job_name`, `task_name`); the legacy machine-minute target.

**Allowed at admission:** planned resources (`plan_cpu`, `plan_mem`, and their machine-fraction counterparts), retry index, `task_type`, and host-grid columns joined with `time_stamp <= decision_time` only.

**Cross-provider exclusions.** `machine_id` is a join key on Alibaba but not a transfer feature: Alibaba identifiers are strings, Google identifiers are integers, and coercing the training column to numeric yields all-NaN. Raw `plan_cpu` is not comparable: Alibaba `plan_cpu` is hundredths of a core on a 96-thread machine (median 100 \(\rightarrow\) fraction 0.0104); Google `cpus_request` is already a fraction of the largest machine in the cell (measured median 0.01041). Comparison uses `plan_cpu_frac` / `plan_mem_frac`. Memory has no shared physical divisor (Google median 0.007 versus Alibaba 0.30) and remains a within-provider relative size.

### 3.6 Classifier and baselines

The selected model is a `HistGradientBoostingClassifier` (`max_depth=6`, `learning_rate=0.1`, `max_iter=100`, `min_samples_leaf=20`, `random_state=0`, `class_weight="balanced"`, no early stopping). It is fit on all 9,000,205 completed primary-rack training instances. Twelve features are used: `task_type` (ordinal-encoded; unseen test level 2 becomes missing) plus eleven numeric columns — planned CPU/memory fractions, instance count, retry indices, and host-grid CPU, memory, disk, and network utilization at or before `decision_time`. Median imputation is fit on train only. `machine_id`, raw `plan_cpu` / `plan_mem`, `mem_gps`, and `mkpi` are dropped at model time. The label is binary; inference is required at admission, so a recurrent model is not used. Output is \(P(\text{fail})\).

**Threshold protocol.** A train-only F1 maximizer lands at 0.352. The operating threshold used for alerts and costing is **0.9**. That cut was read off the **test** precision/recall grid and is not a pre-registered threshold. Ranking metrics (PR-AUC, ROC-AUC) do not use it. Section 4.4 reports 0.5, the train-F1 cut, and 0.9 in the same table.

**Reactive baseline.** Fire at the earlier of (i) retry: `seq_no >= 2` at admission, or (ii) runtime: `decision_time` plus the median successful duration of that `task_type`, with medians fit on **train successes only** (global fallback 10 s). A fire at or after `event_end` does not count.

**Lead time.** On a test failure, lead time is `event_end − alert_time` only if the alert is strictly before `event_end`. If `event_end <= decision_time` (typical when `end_time=0`), lead time is not measurable.

**Metrics**, reported alongside the selected model: precision, recall, and F1 on completed primary-rack time-test instances; always-predict-0 and always-predict-1 (always-1 precision equals the 0.0951% test prevalence, not 37%); and the lead-time distribution rather than a single mean. XGBoost variants were trained on the same split and features and are reported as unselected experiments. The twelve-feature model is evaluated on the replication rack in Section 4.5. Section 4.3 remains the Google diagnostic; the twelve-feature model is not transferred to Google.

### 3.7 Physical cost translation

IT power follows Fan et al. (2007). Water uses WUE with IT energy in the denominator. Facility energy is a separate line item.

| Quantity | Formula | Notes |
|----------|---------|--------|
| IT power | \(P = P_{\mathrm{idle}} + (P_{\mathrm{peak}} - P_{\mathrm{idle}}) \times u\) | Utilization \(u \in [0,1]\). Host-grid CPU is stored as 0–100 and is converted before the product. |
| IT energy | \(\int P\,dt\) over \([\texttt{decision_time}, \texttt{event_end})\) | Convert W·s \(\rightarrow\) kWh. |
| Facility energy | \(\text{IT kWh} \times \text{PUE}\) | Separate line item. Not an input to water. |
| Water | \(\text{IT kWh} \times \text{WUE}\) | Green Grid WP#35: WUE denominator is IT energy. |

A cooling-share coefficient (0.30–0.40) is **not** used. Multiplying IT kWh by a cooling share and then by WUE would double-count and understate water by roughly 2.5–3×. Offsite water embedded in grid electricity (WUE_source) is out of scope: it would require a grid mix that these traces do not provide.

Every cost output is a **range** across physically consistent corners of \((P_{\mathrm{idle}}, P_{\mathrm{peak}}, \text{PUE}, \text{WUE})\). Combinations with \(P_{\mathrm{idle}} > P_{\mathrm{peak}}\) are dropped. With the matched SPEC envelope, idle max (176 W) is below peak min (241 W), so all 16 corners survive. PUE corners affect facility energy only; water depends on idle, peak, and WUE. If a single number is required, it is the midpoint of a stated range, not a point estimate presented without bounds.

**Populations, kept separate.**

- Primary-rack costing-eligible failures: 4,924 in total, of which **204 fall in the time-based test split**. Those 204 events are the headline Alibaba costing sample. Costing every one of them without a classifier is an **oracle upper bound** and is labeled as such in the same table cell.
- Replication-rack costing-eligible failures: 5,123, costed only after primary-rack evaluation. Combining 204 + 5,123 would mix two non-identical stages; they are not a single test set of ~5,300 events. The twelve-feature classifier is scored on this rack in Section 4.5 (13,027,395 rows; ROC-AUC 0.986, PR-AUC 0.861). Whole-rack prevalence is 0.170% versus 0.095% on the primary time-split test, so operating-point precision is not directly comparable; ranking metrics are.
- Google attempts with measured FAIL duration \(\ge\) 60 s: 1,180,014. These events are not costed here. Waste windows would need machine-fraction scaling before an Alibaba watt envelope could be applied.

For policy comparison, Fan is re-integrated over \([\texttt{alert_time}, \texttt{event_end})\) so that a later baseline fire attributes a shorter window than an admission-time alert. False positives are **not** added to avoided waste: killing a healthy job destroys useful compute. That collateral is reported separately, using each false positive’s measured duration on the host grid (20,507 of 20,523 have a positive window; median 84 s; p90 245 s).

### 3.8 Policy comparison

Predicted scores and baseline fire times on the locked test split are joined to the 204 costing-eligible rows. Three accumulators are reported across the same 16 coefficient corners: do-nothing (oracle upper bound over all 204), kill at the classifier alert if that alert is before `event_end`, and kill at the reactive fire time if that fire is before `event_end`. Incremental value is model-minus-baseline on those corners. An independent integration of Fan et al. over the same 204 host-grid windows recovers the do-nothing envelope, which confirms the costing path. Policy results in Section 4.6 use this 204-event table.

### 3.9 Coefficient provenance

An initial idle/peak envelope (80–220 W / 150–450 W) could not be reproduced from the cited SPECpower procedure. Three defects were identified: a vendor-restricted slice of 12 results produced a different envelope; taking the first 15 published results biased the sample by vendor; and multi-node submissions report aggregate watts unless divided by `# of Identical Nodes`. The procedure used here parses 1,116 of 1,156 public SPECpower results, normalizes per node, and filters to 19 two-socket systems with 88–96 hardware threads (Alibaba `cpu_num=96`, \(\pm 10\%\)). The unfiltered 1,116-result envelope (idle 9–1,308 W) mixes blades with four-socket machines and is not the sweep used for costing.

Supercloud CPU timeseries have no watt column. Node-level files have load average and memory, not power. The only measured power is GPU. A linear fit of watts on SM utilization is used solely as a form check (Section 4.2). GPU idle/peak are not written into the CPU coefficient file.

## 4. Results

Sections 4.1–4.3 report corpus statistics, coefficient provenance, and a restricted-feature transfer diagnostic. Sections 4.4–4.6 report the selected classifier, the 16-corner cost sweep, and the policy comparison. Every kilowatt-hour and liter figure is a range. Oracle upper bounds are labeled in the same table cell.

### 4.1 Dataset construction and quality

Table 1 summarizes the two Alibaba racks. Both span timestamps 0–691,190 s (~8.0 days). Host-grid data-gap rate is 0.00% on both racks. Memory-bandwidth and memory-KPI fields are ~78% null in raw `machine_usage`; that is a missing-sensor property of the trace, not a construction defect.

**Table 1.** Alibaba rack coverage. Population: homogeneous 40-machine racks, cluster-trace-v2018, full eight-day window.

| Rack | Machines | Instance rows | Time-grid rows | Mean CPU | p50 CPU | p95 CPU |
|------|----------|---------------|----------------|----------|---------|---------|
| Primary (domain 51) | 40 | 13,088,475 | 414,820 | 40.72% | 40% | 66% |
| Replication (domain 52) | 40 | 13,139,756 | 414,279 | 40.23% | 39% | 65% |

Deltas between racks: CPU 0.50 percentage points, memory ~0.02 percentage points, instance failure rate 0.0016 percentage points. Those gaps are consistent with a second sample of the same hardware class, not with distribution shift.

**Table 2.** Instance-level prediction target. Trainable = Failed / Interrupted / Terminated. Costing-eligible = failed **and** measured waste window \(\ge\) 60 s **and** not imputed.

| Rack | Trainable rows | Failed | Positive rate | Costing-eligible | Censored |
|------|----------------|--------|---------------|------------------|----------|
| Primary | 12,974,617 | 21,780 | **0.1679%** | 4,924 | 113,858 |
| Replication | 13,027,395 | 22,087 | **0.1695%** | 5,123 | 112,361 |

Always-predict-1 precision at this target is 0.17%. Median waste window across all instances is 10 s: the costing set is the long-running tail, which is the only place a kill-at-admission policy can save measurable energy.

Status breakdown on the primary rack: Terminated 12,952,837; Running 113,814; Failed 20,693; Interrupted 1,087; Ready 44. Positive labels use Failed + Interrupted only. Failed instances with `end_time=0` but a parent-task end: 5,303 primary / 5,321 replication. Those carry an imputed upper bound and are **not** costing-eligible.

**Table 3.** Measured costing pool versus minimum waste-window threshold (failed, not imputed). Default for costing is \(\ge\) 60 s.

| Threshold | Primary | Replication |
|-----------|---------|-------------|
| \(\ge\) 1 s | 14,585 | 14,882 |
| \(\ge\) 10 s | 6,879 | 7,116 |
| \(\ge\) 30 s | 5,258 | 5,447 |
| \(\ge\) 60 s (default) | 4,924 | 5,123 |
| \(\ge\) 120 s | 3,848 | 3,966 |
| \(\ge\) 300 s | 2,389 | 2,455 |

**Table 4.** Time-based primary-rack split (`start_time < 518{,}355` s = train). Population: primary rack only. Positive rates are among trainable rows.

| Split | Instances | Positive rate (trainable) | Costing-eligible |
|-------|-----------|---------------------------|------------------|
| Train | 9,083,115 | 0.2000% | 4,720 |
| Test | 4,005,360 | 0.0951% | **204** |

The failure rate drops in the last two days of the trace. That is a property of the held-out test period, not a reason to re-split at random. Table 4 counts every instance in the split, including 30,948 censored test rows still `Running` / `Ready` / `Waiting`. Classification uses the 3,974,412 completed rows. The 204 costing-eligible test failures support an order-of-magnitude range, not a finely sliced subgroup analysis.

Auxiliary machine-minute positive rates, reported only as a diagnostic and not used as a training target: 15 min 23.03% / 23.53%; 30 min 36.84% / 37.59%; 60 min 54.25% / 55.30% (primary / replication).

**Table 5.** Google Cluster Data 2019 attempt table. Population: one cell, first 7 days, 1,718 shards, 210,978,166 events.

| Quantity | Value |
|----------|------:|
| Attempts | 67,934,800 |
| Distinct instances | 16,554,757 |
| Trainable (FAIL+FINISH) | 18,591,767 |
| FAIL | 3,395,635 |
| FAIL / (FAIL+FINISH) | **18.2642%** |
| Costable (FAIL, window \(\ge\) 60 s) | 1,180,014 |
| Multi-attempt fraction of instances | 23.29% |
| Attempts per instance (mean / p50 / max) | 4.104 / 1 / 22,355 |
| Trainable waste-window p50 / p95 | 241 s / 4,660 s |
| `plan_cpu_frac` p50 | 0.01041 |
| `plan_mem_frac` p50 | 0.00726 |
| Start imputed (no SCHEDULE) | 4.17% of attempts |

Raw event mix: SUBMIT 71.2 M, SCHEDULE 67.7 M, EVICT 22.8 M, FAIL 3.77 M, FINISH 15.7 M, KILL 29.7 M. Terminal mix among attempts: evicted 21,813,708; killed 27,529,325; succeeded 15,196,132; failed 3,395,635.

The Google FAIL rate among FAIL+FINISH is ~109× the Alibaba instance-level rate (18.26% vs 0.168%). Google median duration among trainable attempts is 241 s versus Alibaba’s 10 s median across all instances. Those two facts are why Google is an evaluation set rather than a training pool, and why F1 at threshold 0.5 cannot be the headline transfer metric.

On a complete-group hash sample, 39.1% of `(collection_id, instance_index)` keys had more than one terminal, and first-versus-last terminal swung the positive rate from 6.75% to 0.08% (84×). That measurement is why the unit is the attempt.

### 4.2 Coefficient provenance

**Table 6.** Default costing envelope. These are national or hardware-class published ranges, not measurements of the Alibaba rack, and not kilowatt-hour or liter outputs.

| Parameter | Range | Unit | Source | Kind |
|-----------|-------|------|--------|------|
| \(P_{\mathrm{idle}}\) | 40.1–176.0 | W | SPECpower_ssj2008 Active Idle, per node, 19 two-socket 88–96-thread systems | Hardware-matched envelope; not a per-machine mapping (trace hardware is anonymized) |
| \(P_{\mathrm{peak}}\) | 241.0–650.0 | W | SPECpower_ssj2008 100% load, same 19 systems | Same |
| PUE | 1.15–1.40 | — | Shehabi et al. (2024), LBNL-2001637, Figure 4.6: U.S. average ~1.4 in 2023; 2028 scenarios 1.15–1.35 | National/industry average |
| Site WUE | 0.45–0.48 | L/kWh | Shehabi et al. (2024), Figure 4.7 | National average, onsite WUE only |

After per-node normalization and the 96-thread filter, no physically impossible idle > peak corners remain. Idle and peak bounds in the coefficient file are locked to the extracted SPECpower table.

**Supercloud form check.** Linear regression of GPU average watts on average SM utilization (n = 95,182) yields \(R^2 = 0.79\), MAE 15.3 W, RMSE 22.9 W, fitted intercept 30.0 W and slope 150.8 W. This supports using the Fan et al. linear *form* on a different hardware class. It does **not** supply Alibaba CPU coefficients.

**Table 7.** Operator-declared PUE/WUE as a multiplicative scale versus the LBNL range, for a **fixed** IT kWh. `vs_min` / `vs_max` = operator value ÷ LBNL range endpoint. This is not a Fan costing run, not ground-truth validation, and not a kilowatt-hour or liter result. Google 2023 publishes PUE but not a single fleet WUE in the cited report.

| Operator | Year | PUE | WUE (L/kWh) | Facility kWh vs LBNL min/max | Water vs LBNL min/max |
|----------|------|-----|-------------|------------------------------|------------------------|
| Google | 2023 | 1.10 | — | 0.956 / 0.786 | — |
| Microsoft | FY24 | 1.16 | 0.30 | 1.009 / 0.829 | 0.667 / 0.625 |
| Meta | 2023 | 1.08 | 0.18 | 0.939 / 0.771 | 0.400 / 0.375 |
| Equinix | 2024 | 1.39 | 0.95 | 1.209 / 0.993 | 2.111 / 1.979 |
| AWS | 2024 | 1.15 | 0.15 | 1.000 / 0.821 | 0.333 / 0.313 |

Substituting an operator WUE for the LBNL range would reframe the water output from “for the average U.S. datacenter” to “for a facility of this type.” It would not validate the estimate. Equinix’s all-sites WUE is about 2× the LBNL max; AWS 2024 WUE is about one-third of the LBNL min. That spread is why cost outputs remain ranges rather than a point.

### 4.3 Cross-provider diagnostic

A histogram gradient-boosted classifier trained on Alibaba primary-rack instances (200,000-row training cap; positive rate 9.001% in that subsample) is scored on (i) the Alibaba time-based test split and (ii) a stratified Google FAIL+FINISH sample (n = 1,000,111). Features: `plan_cpu_frac`, `plan_mem_frac`, and retry index only. No host-grid window. `machine_id` excluded. Reported metrics are ROC-AUC, PR-AUC, and lift. This is a feature-alignment diagnostic, not the classifier of Section 3.6.

**Table 8.** Restricted-feature transfer diagnostic. F1 at threshold 0.5 on Google is reported only to show that it is confounded by prevalence (Google F1 0.37 > Alibaba F1 0.09 while ROC-AUC falls). Google F1 at 0.5 is not evidence of transfer.

| Population | n | Positive rate | ROC-AUC | PR-AUC | Lift (PR-AUC / base rate) |
|------------|--:|---------------|--------:|-------:|--------------------------:|
| Alibaba time-test (primary rack) | 3,974,412 | 0.0951% | 0.6107 | 0.0991 | 104.2 |
| Google sample (FAIL+FINISH) | 1,000,111 | 18.28% | 0.5095 | 0.2101 | 1.15 |
| Google equalized prevalence | 898,158 | 9.001% | 0.5092 | 0.1128 | 1.25 |

ROC-AUC drop Alibaba \(\rightarrow\) Google: **0.101**. After downsampling Google positives to the diagnostic’s 9.001% training rate, ROC-AUC remains 0.509. The drop is therefore not an artifact of quoting F1 at 0.5. Repeating the same restricted-feature classifier on the full Google attempt table (\(n = 18{,}591{,}767\), FAIL+FINISH prevalence 18.26%) yields ROC-AUC 0.5086, PR-AUC 0.2089, lift 1.14, confirming that Table 8 is not a sampling artifact. Likely contributing causes, not fully separated here, include: (a) the ~109× prevalence gap between the Alibaba instance rate (0.17%) and Google FAIL+FINISH (18.26%) — the 200,000-row training cap itself oversampled positives relative to the true Alibaba rate; (b) a three-feature subset with no host utilization; and (c) `plan_mem_frac` lacking a shared physical divisor. The twelve-feature classifier was not evaluated on Google; no transfer claim is made. The diagnostic does show that comparing raw `plan_cpu`, numeric `machine_id`, or F1 at 0.5 would confound prevalence and units with transfer.

### 4.4 Classifier evaluation

Population: completed primary-rack instances with `start_time >= 518{,}355` s (`n = 3{,}974{,}412`; 3,778 failures; positive rate 0.0951%). Ranking metrics do not use a threshold. Operating-point rows at 0.9 use a cut chosen from this same test grid.

**Table 9.** Primary-rack time-test classification. Always-1 precision equals test prevalence. HistGB PR-AUC 0.802 and ROC-AUC 0.984 are shared across HistGB rows.

| Method | Threshold | Precision | Recall | F1 | FP | TP | PR-AUC | ROC-AUC |
|--------|-----------|----------:|-------:|---:|---:|---:|-------:|--------:|
| Always-0 | — | 0 | 0 | 0 | 0 | 0 | 0.00095 | 0.500 |
| Always-1 | — | 0.00095 | 1 | 0.0019 | 3,970,634 | 3,778 | 0.00095 | 0.500 |
| HistGB | 0.352 (max F1 on **train**) | 0.057 | 0.908 | 0.108 | 56,298 | 3,432 | 0.802 | 0.984 |
| HistGB | 0.5 | 0.089 | 0.902 | 0.162 | 34,798 | 3,407 | 0.802 | 0.984 |
| HistGB (operating) | **0.9 (chosen on test)** | 0.141 | 0.889 | 0.243 | 20,523 | 3,360 | 0.802 | 0.984 |

At threshold 0.9 the alert rate is 0.60% (23,883 alerts; FP/TP = 6.11). The reactive baseline is a time-to-fire rule, not a ranking classifier; it is compared on lead time (Table 10) and on cost (Table 11), not in this table. Constant-classifier accuracy is ~99.9% and is not a useful metric. The train-F1 cut is the threshold that does not use test labels; it doubles false positives relative to 0.9.

XGBoost was trained on the same twelve features and locked split and was **not** selected. The first configuration attains PR-AUC 0.838, recall 0.886, and 13,043 false positives (early stopping halted at iteration 0). The second attains PR-AUC 0.840, recall 0.899, and 32,173 false positives. Lead-time medians matched the histogram gradient-boosted model (17 s). The operating threshold of 0.9 did not transfer well on false-positive count.

**Table 10.** Lead time on primary-rack **test failures** (\(n = 3{,}778\)), not on the 204-event costing sample. A failure is anticipated only if the alert is strictly before `event_end`. 2,502 failures have `event_end <= decision_time` and cannot show positive lead.

| Detector | Anticipated / all failures | Anticipated / 1,276 windowed | Median lead (s) | Mean lead (s) | p90 (s) |
|----------|---------------------------:|-----------------------------:|----------------:|--------------:|--------:|
| HistGB, threshold 0.9 | 1,210 / 3,778 (32.0%) | 1,210 / 1,276 (94.8%) | 17 | 83 | 378 |
| Reactive baseline | 807 / 3,778 (21.4%) | 807 / 1,276 (63.2%) | 19 | 119 | 382 |

Of the 1,210 model-anticipated failures, 1,013 have lead \(<\) 1 min; 22 have 1–5 min; 167 have 5–15 min; 8 have \(\ge\) 15 min. On the 794 failures both detectors catch before `event_end`, the median lead-time delta (model − baseline) is **0 s** (mean 3.5 s; both typically fire at admission). The model-only set is 416; the baseline-only set is 13.

The twelve-feature classifier is evaluated on the replication rack in Section 4.5. It is not evaluated on Google; Section 4.3 remains the Google result.

### 4.5 Physical cost estimates

Population for Tables 11–12: primary-rack time-test failures with `eligible_for_costing=1` (**n = 204**). Sixteen corners of \((P_{\mathrm{idle}}, P_{\mathrm{peak}}, \text{PUE}, \text{WUE})\) from Table 6. Water = IT kWh \(\times\) WUE; facility energy = IT kWh \(\times\) PUE. Do-nothing costs every one of the 204 without a classifier.

**Table 11.** Accumulated physical cost on the 204-event primary-test costing sample. The first row is an **oracle upper bound**. Model and baseline rows count only alerts that fire before `event_end` (197 and 203 of 204). Incremental is model-minus-baseline on the same corners.

| Accumulator | n in time | IT kWh | Facility kWh | Water (L) |
|-------------|----------:|--------|--------------|-----------|
| Do-nothing (**oracle upper bound**) | 204 | 3.39–9.97 | 3.89–13.96 | 1.52–4.79 |
| HistGB kill-at-alert (t = 0.9, test-chosen) | 197 | 3.32–9.78 | 3.82–13.69 | 1.49–4.69 |
| Reactive baseline | 203 | 3.29–9.68 | 3.78–13.55 | 1.48–4.65 |
| Model − baseline (incremental) | — | 0.034–0.097 | 0.039–0.136 | 0.015–0.047 |

The classifier covers fewer costing-eligible events than the baseline (197 vs 203) but attributes slightly more energy, because Fan is integrated from `alert_time` and admission alerts start earlier than some runtime-threshold fires. The incremental gap is two orders of magnitude smaller than the oracle pool. Midpoints of the oracle range are 6.68 IT kWh and 3.16 L; they are midpoints of a published envelope, not facility measurements.

**Table 12.** Collateral if the 20,523 test false positives at threshold 0.9 were killed. This energy is useful compute, **not** avoided waste, and is not added to Table 11.

| Estimate | n | Duration | IT kWh | Facility kWh | Water (L) |
|----------|--:|----------|--------|--------------|-----------|
| Measured Fan on host grid | 20,523 (20,507 with positive window) | median 84 s; p90 245 s | 60.5–179.1 | 69.5–250.7 | 27.2–86.0 |
| Constant-window approximation (10 s \(\times\) mean CPU 40.72%) | 20,523 | 10 s assumed | 6.95–21.0 | 7.99–29.5 | 3.13–10.1 |

The measured false-positive band (60–179 IT kWh) exceeds the entire 204-event oracle waste pool (3.39–9.97 IT kWh).

**Table 13.** Replication rack, failure domain 52, same eight-day window and hardware class. The selected histogram gradient-boosted classifier was **not** trained on these rows. Ranking uses the whole rack (\(n = 13{,}027{,}395\) completed instances, 22,087 failures, prevalence 0.1695%). Costing uses the 5,123 costing-eligible failures. This table is not merged with the 204-event primary-test sample. Whole-rack prevalence is higher than the primary time-split test (0.0951%), so precision at threshold 0.9 is not directly comparable to Table 9; ROC-AUC and PR-AUC are.

| Quantity | Value |
|----------|------:|
| ROC-AUC | 0.986 |
| PR-AUC | 0.861 |
| Precision / recall / F1 at t = 0.9 | 0.441 / 0.907 / 0.593 |
| TP / FP at t = 0.9 | 20,029 / 25,431 |
| Do-nothing (**oracle upper bound**), n = 5,123 | 81.2–244.3 IT kWh; 93.4–342.1 facility kWh; 36.5–117.3 L |
| Model-alerted slice of that pool, n = 5,099 | 80.8–243.1 IT kWh avoided (true-positive accounting only; replication false-positive collateral is not accumulated here) |

Google attempt costing was not run.

### 4.6 Policy comparison

Table 11 is the policy result: on the 204-event sample, a kill-if-\(P(\mathrm{fail}) \ge 0.9\) rule and the reactive baseline recover nearly the same energy. The hypothesis in Section 3.1 is supported in its structural form (nonzero lead time on 32% of test failures and 94.8% of windowed failures; nonzero translated cost on costing-eligible true positives). It is not supported as an operational saving over standard reactive practice once false positives are priced. Executing model kills on healthy jobs would discard more IT energy than the doomed-workload pool contains.

Lead-time reports in Section 4.4 cover 3,778 test failures. Costing and policy cover only the 204-event subset. Those populations are not interchangeable.

## 5. Discussion and Limitations

### 5.1 What the measurements establish

The instance-level base rate is a genuine class-imbalance problem; always-1 F1 is 0.0019. Ranking quality on the frozen primary test set is high (PR-AUC 0.802 versus prevalence 0.00095). Most failures with a recorded end after admission are flagged at admission (median lead 17 s), which is why the model and the retry/runtime baseline often fire at the same instant and why the incremental kilowatt-hour gap is 0.034–0.097 IT kWh.

The causal chain is physically coherent: a doomed instance burns IT energy that is discarded; that energy converts to water through a metric whose denominator is IT energy; the counterfactual is a scheduler action. Two of three coefficient families (PUE, WUE) carry figure-level citations to one report. Idle/peak power is a reproducible matched envelope.

The operational implication is not that the classifier can be deployed to recover the oracle 3.39–9.97 IT kWh. That bound assumes perfect detection and no kills of healthy jobs. At the test-selected threshold, the measured policy recovers almost the same energy as a reactive rule and would discard 60–179 IT kWh if false positives were actually killed. On this corpus, the estimator’s product is a **range-bounded account of waste on the long-running failure tail**, not a demonstrated net saving.

The supported claim remains wasted compute, with water as a downstream consequence. Language of infrastructure-fault prediction would still assert something `machine_meta` cannot support.

### 5.2 Limitations

**Most failed instances have no measured waste window.** Of 21,780 primary-rack failures, 4,924 meet the costing rule. The time-based test split contains 204 of them. Failed rows with `end_time=0` carry a parent-task upper bound, never mixed into measured kWh. Lead time is undefined on 2,502 of 3,778 test failures for the same reason.

**The operating threshold was selected on the test set.** Precision, recall, F1, alert counts, and the 197/204 policy coverage at 0.9 are therefore optimistic relative to a pre-registered cut. PR-AUC, ROC-AUC, and the train-F1 operating point (0.352) do not share that defect. Subsequent work should select the threshold on training or inner validation before evaluating on test.

**Median measurable lead is 17 seconds.** One-minute host resolution and a kill-at-admission policy leave little wall-clock for a human operator. The energy that can be saved is the long tail (167 events at 5–15 min among 1,210 anticipated failures).

**False-positive collateral dominates the waste pool.** At the operating point, FP/TP is 6.11. Measured Fan on those healthy windows is 60–179 IT kWh versus 3.39–9.97 IT kWh of doomed-workload waste.

**No ground-truth validation is available for water.** Liter figures cannot be checked against a facility meter.

**Idle and peak power remain a published envelope, not a facility measurement.** The matched SPEC range (40.1–176 W idle, 241–650 W peak, n = 19) is the dominant source of width in every kWh/liter figure. Supercloud does not close this gap.

**Generalization under distribution shift is not established.** The held-out rack is a replication check on the same week and hardware class (Table 13), not an out-of-distribution test. The Google diagnostic still shows ROC-AUC 0.51 on three features; the twelve-feature model was not transferred to Google.

**PUE and WUE are national averages**, except where operator-published point values are shown as a labeled scale (Table 7).

**This methodology does not validate results against the Frontier supercomputer or any specific facility.**

### 5.3 Impact

The physical cost of a doomed workload is invisible at admission under current monitoring. The estimator attaches a coefficient-bounded range to failures that operators already log, without cooling-plant instrumentation. On this public rack, that range for eight days of the long-running failure tail is 3.39–9.97 IT kWh (**oracle upper bound**) and 3.32–9.78 IT kWh under the classifier. The incremental advantage over a reactive rule is 0.034–0.097 IT kWh. Visibility of that waste is demonstrated; a net saving over reactive practice is not.

The evidence does not support: a guaranteed percentage saving; a replacement for Modelica-grade cooling optimization; a hardware-fault early-warning system; an automatic kill policy that remains energy-positive after false positives; or an environmental disclosure that could be filed without facility-specific coefficients.

What is shown here is an instance-level prediction table with a locked split, cited power and efficiency envelopes, a Google attempt-level alignment, primary-test ranking and lead-time distributions, 16-corner ranges on 204 events with the oracle labeled, false-positive collateral ranges, and replication-rack ranking (ROC-AUC 0.986). Confirming any operational kilowatt-hour or liter saving, any site WUE, any hardware-fault claim, or transfer of the twelve-feature classifier off this hardware class would require a facility study.

### 5.4 Future work

**What this study makes possible.** Before a predicted failure can be costed at admission, three pieces have to exist together: an instance-level label that does not leak post-completion telemetry, a translation from remaining host occupancy into energy and water that is cited rather than assumed, and an accounting identity that charges interrupted successful work as destroyed compute rather than as savings. Public traces and annual PUE/WUE reports supply none of those as a joined object. With them in place, questions that were previously qualitative become quantitative. An operator can ask whether interrupting a class of jobs reduces site energy, in kilowatt-hours, after false interruptions are included, and can state the precision at which that intervention would cease to be a net loss (approximately 15% against the costing-eligible tail on this corpus). A facility study can replace the SPECpower \(\times\) LBNL envelope with measured idle and peak power and site WUE without changing the hypothesis, the split protocol, or the avoided-versus-destroyed identity. A scheduler policy can be pre-registered on training data and then evaluated as an energy decision, not only as a ranking decision. None of those evaluations is well-posed if the only available numbers are an annual site ratio or a classifier accuracy.

**On this corpus.** Evaluate the twelve-feature model on Google attempts using ROC-AUC, PR-AUC, and lift, not F1 at 0.5. Cost Google only after machine-fraction scaling. Select a threshold on training or inner validation and re-report Table 9 operating points. A precision-oriented cut (the grid point at recall \(\ge\) 0.80 gives precision 0.37 and 5,128 false positives) would change the collateral arithmetic and should be pre-registered rather than selected on test. Replication-rack classification is reported in Table 13.

**Operator-published facility WUE.** Substituting a site WUE reframes the output; it does not validate it.

**Facility-grade validation.** Replace swept envelopes with measured idle/peak power and site WUE; compare the estimator to meters. A study of that kind needs admission-time outcomes, host utilization at one-minute resolution or better, measured IT power or a calibrated per-class model, site WUE and PUE for the same period, and a threshold-selection rule analogous to Section 3.1.

**Genuine hardware-fault prediction.** The present work predicts doomed workloads because the Alibaba trace contains no hardware-fault labels.

**Governance and monitoring extension.** Detecting AI training runs from power and network signals (Hardware-Level Governance of AI Compute, 2026) is structurally related and is not a current capability. That paper uses power and network; the Supercloud artifact used here is GPU power only.

**Inherent data limitation.** No public dataset joins fault events to measured facility water. Results remain coefficient-bounded ranges.

Hardware-failure corpora such as Backblaze drive statistics were considered and rejected: they have no join key to the Alibaba workload.

## 6. Conclusion

A doomed batch instance burns energy that is discarded. On a 40-machine Alibaba rack with a time split locked before model selection, that waste is detectable at admission with high ranking quality (PR-AUC 0.802) and, for failures with a recorded end, a median 17 s lead. Translating the 204 test failures that have a measured window of at least 60 s through a published SPECpower \(\times\) LBNL envelope yields an **oracle upper bound** of 3.39–9.97 IT kWh and 1.52–4.79 L of onsite water. A histogram gradient-boosted policy at a test-selected threshold of 0.9 recovers 3.32–9.78 IT kWh; a reactive retry-or-runtime rule recovers 3.29–9.68 IT kWh. The incremental gap is 0.034–0.097 IT kWh. Killing the model’s 20,523 false positives would discard 60–179 IT kWh of useful work.

FPCE therefore makes the physical cost of inaction visible as a range. On this corpus it does not outperform ordinary reactive practice by an amount that remains positive after false positives are counted, and it does not measure facility water. Those are the results the data support.


## Code and Data

- **Code:** accompanying `fpce` repository (ingestion, feature contract, classifier, Fan translation, policy accumulation, SPECpower and Supercloud provenance).
- **Reproduction:** `pip install -e ".[dev]"` then `bash scripts/run_ingest.sh`. Checks: `python scripts/validate_config.py && pytest`. Serialized classifier and test scores: `fpce-role-b-freeze`. Costing: `fpce-role-c-cost`. Policy table: `fpce-policy-sim`.
- **Supporting artifacts:** `params/physical_cost.toml`, `params/feature_contract.json`, time-split JSON, SPECpower envelope parquet, and the JSON reports cited in Sections 4–5.
- **Traces:** Alibaba Cluster Trace Program v2018 (OSS mirror). Google ClusterData2019 (one cell, one week). MIT Supercloud HPCA’22 GPU power (AWS Registry of Open Data).

## Author Contributions

[to be completed]

## References

Alibaba. (2018). *Alibaba Cluster Trace Program.* https://github.com/alibaba/clusterdata

Fan, X., Weber, W. D., & Barroso, L. A. (2007). Power provisioning for a warehouse-sized computer. *ISCA ’07.* https://research.google/pubs/power-provisioning-for-a-warehouse-sized-computer/

Google. (2019). *ClusterData 2019 trace documentation.* https://github.com/google/cluster-data/blob/master/ClusterData2019.md

Google. (2024). *Google 2024 Environmental Report.* https://www.gstatic.com/gumdrop/sustainability/google-2024-environmental-report.pdf

Guo, J., Chang, Z., Wang, S., Ding, H., Feng, Y., Mao, L., & Bao, Y. (2019). Who limits the resource efficiency of my datacenter: An analysis of Alibaba datacenter and warehouse-scale computers. *SoCC ’19.*

Hardware-Level Governance of AI Compute. (2026). arXiv:2604.04712. https://arxiv.org/abs/2604.04712

IRJMETS. (2025). Enhancing water sustainability in data centers. https://www.irjmets.com/uploadedfiles/paper/issue_4_april_2025/73401/final/fin_irjmets1745228941.pdf

Jadhav, S., & Liu, Z. (2026a). Digital twin-based cooling system optimization for data center. arXiv:2603.01198. https://arxiv.org/abs/2603.01198

Jadhav, S., & Liu, Z. (2026b). Co-design optimization for data center cooling system via digital twin. arXiv:2605.15516. https://arxiv.org/abs/2605.15516

Jadhav, S., & Liu, Z. (2026c). Data center life cycle co-design optimization. arXiv:2606.15408. https://arxiv.org/abs/2606.15408

EPJ Special Topics. (2026). Artificial intelligence and digital twins for failure prediction in data center cooling systems: A comprehensive literature review (2018–2026). https://link.springer.com/article/10.1140/epjs/s11734-026-02411-x

Microsoft. (2024). Measuring energy and water efficiency (PUE/WUE). https://datacenters.microsoft.com/sustainability/efficiency/

Meta. (2024). *Meta 2024 Sustainability Report.* https://sustainability.atmeta.com/wp-content/uploads/2024/08/Meta-2024-Sustainability-Report.pdf

Equinix. (2024). Sustainability data summary. https://www.equinix.com/resources/data-sheets/sustainability-data-summary

Amazon. (2024). *2024 Amazon Sustainability Report, AWS Summary.* https://sustainability.aboutamazon.com/2024-amazon-sustainability-report-aws-summary.pdf

Samsi, S., Weiss, M., Bestor, D., et al. (2021). The MIT Supercloud Dataset. *IEEE HPEC 2021.* arXiv:2108.02037. https://arxiv.org/abs/2108.02037

MIT Supercloud Dataset, AWS Registry of Open Data: https://registry.opendata.aws/dcc/

ScienceDirect. (2026). Data center cooling system fault detection and diagnosis: A comprehensive review and outlook. https://www.sciencedirect.com/science/article/abs/pii/S0360544226019614

Shehabi, A., et al. (2024). *2024 United States Data Center Energy Usage Report.* Lawrence Berkeley National Laboratory, LBNL-2001637. https://eta-publications.lbl.gov/sites/default/files/2024-12/lbnl-2024-united-states-data-center-energy-usage-report_1.pdf

Standard Performance Evaluation Corporation. SPECpower_ssj2008 results. https://www.spec.org/power_ssj2008/results/

The Green Grid. (2011). *Water Usage Effectiveness (WUE): A Green Grid data center sustainability metric* (White Paper #35). https://www.thegreengrid.org/en/resources/library-and-tools/238-WP%2335---Water-Usage-Effectiveness-(WUE):-A-Green-Grid-Data-Center-Sustainability-Metric

Tirmazi, M., Barker, A., Deng, N., et al. (2020). Borg: The next generation. *EuroSys ’20.* https://doi.org/10.1145/3342195.3387517

---

## Appendix A. Design revisions relative to the original proposal

This appendix records where the study diverges from the original proposal and why. Most changes follow from measurements on the ingested traces rather than from a change of research question.

| # | Area | Original proposal | This paper | Evidence |
|---|------|----------------|---------------|---------------------|
| 1 | Primary dataset | Google Cluster Data for training; Alibaba held out | Alibaba v2018 for training; second Alibaba rack held out; Google 2019 as evaluation | 2011 Google trace requires a gcloud questionnaire. Full 2019 eight-cell scan is 2.4 TiB. Google FAIL/(FAIL+FINISH) is 18.26% vs 0.17% at Alibaba instance level (~109×). |
| 2 | Prediction unit | Machine-minute window, 30-minute failure horizon | Single batch instance, decision at admission | ~550 instance failures per machine over 8 days \(\rightarrow\) 36.84% positive at 30 min. Always-1 matches that precision. Instance-level rate is 0.1679%. |
| 3 | Positive class | Included `Waiting` | `Failed` + `Interrupted` only | `Waiting` is not an outcome in the Alibaba schema. |
| 4 | SPEC Power role | Benchmark curves mapped to trace machine classes | Envelope bounding a sensitivity sweep only | Trace anonymizes hardware identity. |
| 5 | Water conversion | IT kWh \(\times\) cooling share (0.30–0.40) \(\times\) WUE | IT kWh \(\times\) WUE, directly | Green Grid WP#35: WUE denominator is IT energy. Cooling share double-counted and understated water ~2.5–3\(\times\). |
| 6 | Cooling share | “Per prior cooling-energy studies,” no citation | Removed | Unnecessary once the double-count was identified. |
| 7 | Green Grid citation | “To be confirmed” | The Green Grid (2011), WP#35 | Located and verified. |
| 8 | Facility energy | Not modelled | PUE 1.15–1.40 as a **separate** line item | LBNL 2024 Figure 4.6. Never multiplied into water. |
| 9 | Coefficient sweep | 16 corners, then a 12-corner artifact | 16 corners (matched envelope does not overlap idle/peak) | After per-node normalization, idle max 176 W < peak min 241 W. |
| 10 | Second rack | “OOD evaluation” / “generalization check” | **Replication** check | CPU 40.72% vs 40.23%; failure rate 0.1679% vs 0.1695%. Same window, same hardware class. |
| 11 | Framing of the anomaly | Systemic / cooling-fault detection | Doomed-workload detection | `machine_meta`: zero status transitions (17,587 `USING`, 5 `IMPORT_INSTALLING`). |
| 12 | Leakage control | Not addressed | Machine-readable allow/deny contract | Post-completion telemetry and identity columns denied. |
| 13 | SPEC provenance | Idle 80–220 W, peak 150–450 W, not independently reproduced | Idle 40.1–176 W, peak 241–650 W from 19 matched systems | A 12-result vendor-restricted slice produced 47.4–128 / 206–827 W and did not divide by `identical_nodes`. |
| 14 | SPEC sample | 15 result files | 1,116 parsed of 1,156 linked results | Index page exposes 1,156 `.txt` reports. |
| 15 | `end_time=0` costing | Duration either dropped or imputed from the parent task | Measured window remains 0; parent-task upper bound stored separately and never mixed into costing | 6,970 of 21,780 primary failures have `end_time=0`. |
| 16 | Google unit | One row per Borg instance (first SCHEDULE, first terminal) | One row per **attempt** | 39.1% of keys had more than one terminal; first vs last swung positives 6.75% \(\rightarrow\) 0.08%. |

| Limitation | Status | Remarks |
|------------|--------|---------|
| Power coefficients unreproducible or too generic | Addressed | Matched SPECpower envelope with a provenance check |
| Linear form untested on measured power | Addressed as a form check | Supercloud GPU \(R^2 = 0.79\); coefficients do not transfer |
| Classifier and cost on primary test | Reported | Tables 9–12; threshold 0.9 selected on test |
| Incremental cost vs reactive baseline | Reported | 0.034–0.097 IT kWh on 204 events |
| False-positive collateral | Reported | 60–179 IT kWh if 20,523 false positives were killed |
| Replication classifier | Reported | 13.0M rows; ROC-AUC 0.986, PR-AUC 0.861; oracle 81–244 IT kWh |
| Twelve-feature model on Google | Open | Diagnostic ROC-AUC 0.51 on three features |
| Prevalence not comparable (~109\(\times\)) | Open (metric choice) | ROC-AUC / PR-AUC / lift; F1 at 0.5 is not a transfer metric |
| Feature scales incompatible | Addressed for CPU | Google `plan_cpu_frac` p50 0.01041 vs Alibaba `plan_cpu/100/96` = 0.0104. Memory remains open. |
| Small costing sample (204 test events) | Partially addressed | Replication oracle pool 5,123; Google costing not run |
| Operating threshold selected on test | Open | Select on training or inner validation and re-report |
| Water coefficient is a national average | Open | Operator scale comparison in Table 7 is not validation |
| No joint fault-and-water ground truth | Unresolvable with public data | Stated limitation |

## Appendix B. Use of language models

Claude Opus 5.0 and Cursor Grok 4.6 were used for pipeline implementation and debugging, and for drafting this manuscript from repository artifacts (quality reports, coefficient files, proposal text, and source comments).

Every quantitative claim in Sections 4.1–4.6 was checked against the corresponding reports in the repository. Oracle upper bounds in Tables 11 and 13 are labeled in the table cell. Table 8 remains a restricted-feature diagnostic and is not presented as the selected classifier. No language model produced a kilowatt-hour or liter figure.

Language-model assistance does not substitute for independent verification. Reproduction should start from the checksummed tables and the commands in the Code and Data section.
