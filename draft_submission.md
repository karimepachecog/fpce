# What a Failure Costs: Energy and Water Wasted by Compute That Runs to Its Own Failure

**Subtitle:** Linking doomed-workload detection to the physical cost of inaction.

**Authors:** Karime Pacheco Gallegos, Hannia (?), Santiago (?), Meribeth (?)

**Code and Data:** Accompanying repository `fpce`. Raw Alibaba traces are acquired from the public Beijing OSS mirror. Google Cluster Data 2019 is a one-cell, one-week export of lifecycle events. Coefficient sources are cited in `params/physical_cost.toml`. Processed tables and checksums are documented with the repository.

---

## Abstract

Warehouse-scale computers expend energy and cooling water on batch instances that will fail, but that physical cost remains invisible under reactive monitoring and static PUE/WUE reporting. We propose a Fault-triggered Physical Cost Estimator (FPCE) that predicts doomed batch instances at admission and translates their remaining runtime into a coefficient-bounded range of IT energy (kilowatt-hours), facility energy (kilowatt-hours), and onsite cooling water (liters). The claim is instance-level doomed-workload detection, not hardware-fault prediction: the Alibaba cluster-trace-v2018 records zero machine-status transitions.

A homogeneous 40-machine rack yields 13,088,475 instance events (trainable failure rate 0.1679%; 4,924 costing-eligible failures with measured duration of at least 60 seconds). A frozen time split holds 4,005,360 test instances, of which 204 are costing-eligible. A second rack of the same hardware class is reserved as a replication check, not a distribution-shift test. Power coefficients are a hardware-matched SPECpower envelope (idle 40.1–176 W, peak 241–650 W; n = 19 two-socket systems); PUE (1.15–1.40) and site WUE (0.45–0.48 L/kWh) follow the 2024 LBNL U.S. Data Center Energy Usage Report. Google Cluster Data 2019 was aligned as 67.9 million Borg attempts (18.26% FAIL among FAIL+FINISH). Classifier performance, lead-time distributions, and kilowatt-hour/liter ranges are specified in the methods and left for the corresponding results subsections. No public dataset joins workload failure to measured facility water; physical-cost figures, when reported, will be ranges, not facility measurements.

## 1. Introduction

A batch instance that will fail still occupies a machine until it does. Between admission and that eventual failure, the instance draws IT power, the facility supplies overhead energy, and evaporative or hybrid cooling plants withdraw water. Under current practice that physical cost is not attached to the failure. Operators see resource-level alerts (retry-until-success, a runtime threshold, a utilization page). Sustainability reporting sees static annual ratios — power usage effectiveness (PUE) and water usage effectiveness (WUE) — that do not distinguish a doomed workload from a successful one. The energy burned between a decision that could have been taken at admission and the moment a reactive rule would have acted is discarded work. It is also, at present, uncounted work.

This work asks a narrower question than “optimize a cooling plant” and a more specific one than “detect anomalies.” For a fixed anomaly class (batch-instance failure) and a fixed reactive baseline (a retry-count or runtime-threshold rule), does a predictor that fires at instance admission produce a positive lead time on a measurable share of held-out failed instances, and is the physical cost accumulated during that remaining runtime strictly greater than zero when translated through a published coefficient envelope? The magnitude of any saving is treated as an experimental result, not an a priori percentage. No claim is made that the resulting kilowatt-hour or liter figures are measurements from an operating facility.

An earlier framing of this problem was infrastructure- and cooling-fault detection. Contact with the Alibaba cluster-trace-v2018 forced a revision. The `machine_meta` table records zero status transitions across 4,034 machines (17,587 rows `USING`, 5 `IMPORT_INSTALLING`). There is no hardware-fault or cooling-fault ground truth in this corpus. A machine-minute prediction target with a 30-minute horizon is also not an anomaly: each machine sees on the order of 550 instance failures over eight days, so about 37% of 30-minute windows are positive and a constant “always fail” classifier matches that precision. The defensible unit is the batch instance. Instance-level `Failed` / `Interrupted` is 0.1679% of completed instances on the primary rack. Killing a doomed instance at admission is an action production schedulers already take. The claim supported by these traces is therefore doomed-workload detection and the physical cost of letting those workloads run. That is the best-supported claim given public data, not a statement of the problem’s ceiling: facility telemetry with maintenance records would legitimately reopen the infrastructure-level question (Section 5.4).

This paper makes three contributions on the evaluation setting, and specifies the remaining experiments in Methods so that later results cannot be mistaken for claims already made.

1. **A leakage-controlled, instance-level prediction table** from Alibaba cluster-trace-v2018, with a frozen time split, a held-out replication rack, an explicit allow/deny feature contract, and a costing-eligibility rule that refuses to invent a duration when `end_time` is missing.
2. **A cited coefficient registry** in which idle and peak power are a reproducible hardware-matched SPECpower envelope; PUE and site WUE are figure-level citations to a single national report (Shehabi et al., 2024); and water is \(\text{IT kWh} \times \text{WUE}\) with no cooling-share multiplier, because WUE’s denominator is already IT energy (The Green Grid, 2011).
3. **A cross-provider attempt table** from Google Cluster Data 2019, with the unit of analysis (Borg attempt, not Borg instance lifetime), resource-scale alignment (`plan_cpu_frac`), and evaluation metrics chosen so that a ~109× prevalence gap cannot be mistaken for a successful transfer.

The classifier, the Fan et al. (2007) cost translation, and the replay comparison against a reactive baseline are defined in Section 3. Their numerical results occupy Sections 4.4–4.6. Oracle ceilings, where computed, will be labeled as such in the same table cell as the number; ranges will remain ranges.

## 2. Related Work

Three bodies of work bound what this project can honestly claim.

**Reactive monitoring and static efficiency ratios.** Production operations rely on threshold tools (Prometheus, Nagios, and related stacks) that fire after a signal crosses a limit. They do not attach a physical cost to the interval between an earlier possible decision and that firing. Parallel to that, operators and auditors report PUE and WUE as annual site ratios. Those ratios are known to extrapolate poorly outside the conditions under which they were derived, and they do not attribute water or energy to a specific failed job. The present work does not replace either practice. It asks whether a predicted instance failure can be given a coefficient-bounded cost that those practices currently leave unassigned.

**Physics-informed digital twins for cooling optimization.** Jadhav and Liu (2026a) developed a Modelica-based digital twin of the liquid cooling plant at the Frontier exascale supercomputer, validated against a full year of ten-minute operational data. Jadhav and Liu (2026b) extended this to co-design across coolant-distribution-unit partitions, reporting approximately 35% annual cooling-energy savings; a subsequent paper extends the framework to full life-cycle optimization (Jadhav and Liu, 2026c). This is the most directly relevant prior art, and it is also the reason this project refuses the term “digital twin.” Those systems are physics-simulated, facility-validated, and annual-scale. They optimize a cooling plant given proprietary Oak Ridge National Laboratory telemetry that is not available here, and they do not respond to an individual predicted workload failure in real time. Calling a coefficient lookup a digital twin would overstate fidelity relative to that line of work.

**Failure prediction and water-aware control.** Reviews in *EPJ Special Topics* (2026) and *ScienceDirect* (2026) survey AI and digital-twin approaches to cooling-failure prediction, indicating that general novelty claims in that space require a specific gap. An applied study (IRJMETS, 2025) reports LSTM- and genetic-algorithm-based real-time WUE optimization on two years of operational Tier IV data, reducing WUE from 1.8 to 1.35 L/kWh. That result shows that water-aware control is achievable on facility data. It optimizes continuously rather than attaching a cost estimate to a specific predicted fault, and it uses operational telemetry this project does not have.

**Power models and cluster traces.** Fan, Weber, and Barroso (2007) established the linear utilization-to-power model used here: \(P = P_{\mathrm{idle}} + (P_{\mathrm{peak}} - P_{\mathrm{idle}}) \times u\). SPECpower_ssj2008 supplies the idle and peak envelope, after per-node normalization, for servers in the same socket/thread class as the Alibaba rack. The Alibaba Cluster Trace Program v2018 provides per-instance batch status and machine utilization (Guo et al., 2019; Alibaba, 2018). Google Cluster Data 2019 documents Borg’s instance lifecycle, including eviction and reschedule (Tirmazi et al., 2020). The MIT Supercloud Dataset (Samsi et al., 2021) provides measured GPU power used only as a check on the linear *form*, not as a source of CPU coefficients.

**Gap.** No work identified in this review ties a specific predicted anomaly to a counterfactual physical cost, computed from public rather than facility-proprietary telemetry, and reported with explicit uncertainty rather than as a point estimate. That is the gap this project addresses. It is a narrower and more defensible claim than general-purpose digital-twin sustainability optimization, and narrower than hardware-fault detection. Adjacent work on detecting AI training runs from power and network signals (Hardware-Level Governance of AI Compute, 2026) is structurally related and is reserved as future work (Section 5.4), not as a current capability.

## 3. Methods

### 3.1 Hypothesis and evaluation protocol

**Hypothesis.** For batch-instance failure, and against a reactive baseline that fires on retry count or on runtime versus a task-level median, a predictor that decides at instance admission (`decision_time = start_time`) will produce a positive lead time on a measurable proportion of held-out failed instances, and the physical cost accumulated during that remaining runtime will be strictly greater than zero on those events when translated through the coefficient envelope in Section 3.7.

No percentage reduction is claimed in advance. The lead-time distribution and the kilowatt-hour and liter ranges are experimental outputs, not inputs to the design.

**Evaluation freeze.** The primary-rack time split is frozen before model selection (`split_timestamp = 518{,}355` s). The replication rack (failure domain 52) is unused for training, hyperparameter search, or costing until primary-rack evaluation is frozen. Google attempts are a cross-provider evaluation set, not a training pool. A diagnostic transfer experiment on a restricted feature set (Section 4.3) does not lift that freeze and is not a substitute for the full classifier in Section 3.6.

### 3.2 Datasets

**Alibaba cluster-trace-v2018 (primary).** Eight-day production trace with machine-level utilization (`machine_usage`) and per-instance batch status (`batch_instance`), including explicit `Failed` labels. Data are acquired from the public Beijing OSS mirror. Training uses a homogeneous 40-machine rack in failure domain 51 (96 hardware threads, `mem_size` 100). Host utilization is resampled to a one-minute grid per rack and joined to instances only at timestamps \(\le\) `decision_time`.

**Alibaba cluster-trace-v2018, held-out rack (replication).** A second homogeneous 40-machine rack from failure domain 52, same eight-day window, same hardware specification. Measured CPU utilization is 40.23% versus 40.72% on the primary rack; instance-level failure rates are 0.1695% versus 0.1679%. This is a replication check across machine sets, not an out-of-distribution test. Genuine shift testing would require a different time window or a different hardware class.

**Google Cluster Data 2019 (cross-provider evaluation).** One cell, first seven days after the 600 s origin, exported as 1,718 parquet shards (3.4 GB, 210,978,166 lifecycle events). The modelling unit is a Borg *attempt* (a SCHEDULE paired with the next terminal event), not a `(collection_id, instance_index)` lifetime: Borg reschedules after EVICT/KILL, and collapsing to one row per instance makes the label an arbitrary choice of first versus last terminal. The 2011 Google trace is unused.

**Coefficient sources (not telemetry).** Fan et al. (2007) for the power-model form. SPECpower_ssj2008 public results for idle/peak envelopes, filtered to two-socket servers with 88–96 hardware threads and divided by `# of Identical Nodes`. Shehabi et al. (2024), LBNL-2001637, Figure 4.6 (PUE) and Figure 4.7 (site WUE). The Green Grid White Paper #35 (2011) for the WUE identity. Operator ESG point values (Google, Microsoft, Meta, Equinix, AWS) are stored as a multiplicative scale comparison against LBNL, not as a substitute for the national-average costing range. MIT Supercloud GPU power traces are used only to fit the linear form on SM utilization versus watts.

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

**Decision time.** The kill/keep decision is taken at admission (`decision_time = start_time`), before any of the instance’s own telemetry exists. Host context is therefore a trailing window of *machine* utilization ending at `decision_time`, not post-completion instance CPU or memory averages.

**Why not machine-minute.** An auxiliary horizon column remains on the host grid for diagnostics. At 30 minutes its positive rate is 36.84% (primary) / 37.59% (replication). Always-predict-1 precision at that target is ~37%. That column is denied as a training feature. The classifier trains on `failed` among completed (non-censored) instances only.

### 3.4 Corpus construction

Alibaba traces are filtered to two homogeneous 40-machine racks, converted to parquet, and resampled to a one-minute host grid. Instance events join planned resources from the parent task, attach the binary label, and compute measured waste windows. A time-based 75/25 split on `start_time` is frozen. SPECpower public results are scraped, normalized per node, and filtered to the 96-thread envelope; a regression test fails if the coefficient file drifts from that parquet. Supercloud GPU power is fit as a form check and is not copied into the CPU envelope.

Google shards are unordered: every file spans the full `collection_id` range, so shard-at-a-time aggregation would split instances. An out-of-core window pairs each terminal with the preceding SCHEDULE and collapses extra terminals that share a schedule. `attempt_index` is the homolog of Alibaba `seq_no`. EVICT and KILL are recorded but excluded from training, so a model trained on Alibaba Failed/Terminated is scored on Google FAIL versus FINISH rather than on preemption. A stratified sample of FAIL+FINISH attempts is retained for the diagnostic in Section 4.3; the full attempt table is the evaluation set for the classifier in Section 4.4.

### 3.5 Leakage control

A machine-readable feature contract lists columns allowed at admission and columns denied because they are outcomes, post-completion telemetry, or identity keys.

**Denied (not exhaustive):** instance telemetry known only after completion (`cpu_avg`, `cpu_max`, `mem_avg`, `mem_max`); outcome timestamps and derivatives (`end_time`, waste-window lengths); identity columns that leak across sibling instances of the same failing job (`instance_name`, `job_name`, `task_name`); the legacy machine-minute target.

**Allowed at admission:** planned resources (`plan_cpu`, `plan_mem`, and their machine-fraction counterparts), retry index, `task_type`, and host-grid columns joined with `time_stamp <= decision_time` only.

**Cross-provider exclusions.** `machine_id` is a join key on Alibaba but not a transfer feature: Alibaba identifiers are strings, Google identifiers are integers, and coercing the training column to numeric yields all-NaN. Raw `plan_cpu` is not comparable: Alibaba `plan_cpu` is hundredths of a core on a 96-thread machine (median 100 \(\rightarrow\) fraction 0.0104); Google `cpus_request` is already a fraction of the largest machine in the cell (measured median 0.01041). Comparison uses `plan_cpu_frac` / `plan_mem_frac`. Memory has no shared physical divisor (Google median 0.007 versus Alibaba 0.30) and remains a within-provider relative size.

### 3.6 Classifier and baselines

A gradient-boosted classifier is used rather than a recurrent model: the label is binary per instance, and inference is required at admission. Features are restricted to the contract in Section 3.5. The model outputs \(P(\text{fail})\). Lead time for a costing-eligible true positive is the interval between `decision_time` and the moment a reactive baseline (retry count, or runtime versus a task-level median) would have acted on the same instance.

Metrics, reported in the same table as the proposed model:

- Precision, recall, and F1 on completed instances of the primary-rack **time-based** test split.
- Always-predict-0 and always-predict-1. Always-1 precision at instance level is 0.17%, not 37%.
- Lead-time distribution on costing-eligible true positives, reported as a distribution, not compressed into a single mean.
- One replication pass on the domain-52 rack after the primary freeze, reported regardless of outcome, labeled as replication across machines, not as distribution-shift generalization.
- Cross-provider evaluation on Google attempts using ROC-AUC, PR-AUC, and lift \(=\) PR-AUC / base rate. F1 at threshold 0.5 is confounded by the prevalence gap and is not the headline transfer metric.

Section 4.3 reports a diagnostic on three overlapping columns only (`plan_cpu_frac`, `plan_mem_frac`, retry index), without a host-grid window. That diagnostic is not the classifier evaluated in Section 4.4.

### 3.7 Physical cost translation

IT power follows Fan et al. (2007). Water uses WUE with IT energy in the denominator. Facility energy is a separate line item.

| Quantity | Formula | Notes |
|----------|---------|--------|
| IT power | \(P = P_{\mathrm{idle}} + (P_{\mathrm{peak}} - P_{\mathrm{idle}}) \times u\) | Utilization \(u \in [0,1]\). Host-grid CPU is stored as 0–100 and is converted before the product. |
| IT energy | \(\int P\,dt\) over \([\texttt{decision_time}, \texttt{event_end})\) | Convert W·s \(\rightarrow\) kWh. |
| Facility energy | \(\text{IT kWh} \times \text{PUE}\) | Separate line item. Not an input to water. |
| Water | \(\text{IT kWh} \times \text{WUE}\) | Green Grid WP#35: WUE denominator is IT energy. |

A cooling-share coefficient (0.30–0.40) is **not** used. Multiplying IT kWh by a cooling share and then by WUE would double-count and understate water by roughly 2.5–3×. WUE_source (offsite water embedded in grid electricity) is out of scope: it would require a grid mix this project does not have.

Every cost output is a **range** across physically consistent corners of \((P_{\mathrm{idle}}, P_{\mathrm{peak}}, \text{PUE}, \text{WUE})\). Combinations with \(P_{\mathrm{idle}} > P_{\mathrm{peak}}\) are dropped. With the matched SPEC envelope, idle max (176 W) is below peak min (241 W), so all 16 corners survive. PUE corners affect facility energy only; water depends on idle, peak, and WUE. If a single number is required for a non-technical audience, it is the midpoint of a stated range, never a lone point estimate.

**Populations, kept separate.**

- Primary-rack costing-eligible failures: 4,924 total, of which **204 are in the time-based test split**. The 204 are the headline Alibaba costing sample once true positives are identified on the test set. Costing every one of the 204 without a classifier is an **oracle / theoretical ceiling** and will be labeled as such in the same sentence or table cell.
- Replication-rack costing-eligible failures: 5,123. Additional costing pool only after the primary evaluation freeze. Combining 204 + 5,123 must be described as two non-identical evaluation stages, not as a single test set of ~5,300 events.
- Google attempts with measured FAIL duration \(\ge\) 60 s: 1,180,014. Exploratory. Waste windows are wall-clock seconds of a Borg attempt and must be scaled by machine-fraction capacity; Alibaba watt envelopes are not applied to Google durations raw.

### 3.8 Replay evaluation

Held-out trace time is replayed in simulated real time. At each instance’s `decision_time` the classifier scores the instance; on costing-eligible true positives the cost range is accumulated against the reactive baseline. A kill-if-\(P(\mathrm{fail}) > \tau\) policy is compared with that baseline on the frozen primary test population. The replay is the experimental accumulator, not a demonstration interface.

### 3.9 Coefficient provenance

Idle and peak power in an earlier draft (80–220 W / 150–450 W) were cited to a summarizer that did not reproduce those bounds. Three defects were identified: the cited function produced a different envelope on a 12-result vendor-restricted slice; a first-15 scrape was vendor-biased; multi-node submissions report aggregate watts and were not divided by `# of Identical Nodes`. The current procedure parses 1,116 of 1,156 public SPECpower results, normalizes per node, and filters to 19 two-socket systems with 88–96 hardware threads (Alibaba `cpu_num=96`, \(\pm 10\%\)). The unfiltered 1,116-result envelope (idle 9–1,308 W) mixes blades with four-socket machines and is not the sweep used for costing.

Supercloud CPU timeseries have no watt column. Node-level files have load average and memory, not power. The only measured power is GPU. A linear fit of watts on SM utilization is used solely as a form check (Section 4.2). GPU idle/peak are not written into the CPU coefficient file.

## 4. Results

Sections 4.1–4.3 report corpus statistics, coefficient provenance, and a diagnostic transfer experiment. Sections 4.4–4.6 hold the classifier, physical-cost ranges, and replay comparison. No kilowatt-hour or liter figure is reported in this draft, including no oracle ceiling.

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

**Table 4.** Frozen primary-rack time split (`start_time < 518{,}355` s = train). Population: primary rack only. Positive rates are among trainable rows.

| Split | Instances | Positive rate (trainable) | Costing-eligible |
|-------|-----------|---------------------------|------------------|
| Train | 9,083,115 | 0.2000% | 4,720 |
| Test | 4,005,360 | 0.0951% | **204** |

The failure rate drops in the last two days of the trace. That is a property of the held-out test set, not a reason to re-split at random. The 204 costing-eligible test failures support an order-of-magnitude range, not a finely sliced subgroup analysis.

Auxiliary machine-minute positive rates, for the record and not as a target: 15 min 23.03% / 23.53%; 30 min 36.84% / 37.59%; 60 min 54.25% / 55.30% (primary / replication).

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

After per-node normalization and the 96-thread filter, no physically impossible idle > peak corners remain. A provenance test locks the idle/peak ranges to the SPEC parquet.

**Supercloud form check.** Linear regression of GPU average watts on average SM utilization (n = 95,182) yields \(R^2 = 0.79\), MAE 15.3 W, RMSE 22.9 W, fitted intercept 30.0 W and slope 150.8 W. This supports using the Fan et al. linear *form* on a different hardware class. It does **not** supply Alibaba CPU coefficients.

**Table 7.** Operator-declared PUE/WUE as a multiplicative scale versus the LBNL range, for a **fixed** IT kWh. `vs_min` / `vs_max` = operator value ÷ LBNL range endpoint. This is not a Fan costing run, not ground-truth validation, and not a kWh or liter result. Google 2023 publishes PUE but not a single fleet WUE in the cited report.

| Operator | Year | PUE | WUE (L/kWh) | Facility kWh vs LBNL min/max | Water vs LBNL min/max |
|----------|------|-----|-------------|------------------------------|------------------------|
| Google | 2023 | 1.10 | — | 0.956 / 0.786 | — |
| Microsoft | FY24 | 1.16 | 0.30 | 1.009 / 0.829 | 0.667 / 0.625 |
| Meta | 2023 | 1.08 | 0.18 | 0.939 / 0.771 | 0.400 / 0.375 |
| Equinix | 2024 | 1.39 | 0.95 | 1.209 / 0.993 | 2.111 / 1.979 |
| AWS | 2024 | 1.15 | 0.15 | 1.000 / 0.821 | 0.333 / 0.313 |

Substituting an operator WUE for the LBNL range would reframe the water output from “for the average U.S. datacenter” to “for a facility of this type.” It would not validate the estimate. Equinix’s all-sites WUE is about 2× the LBNL max; AWS 2024 WUE is about one-third of the LBNL min. That spread is why cost outputs remain ranges rather than a point.

### 4.3 Cross-provider diagnostic

A histogram gradient-boosted classifier trained on Alibaba primary-rack instances (200,000-row training cap; positive rate 9.001% in that subsample) is scored on (i) the Alibaba time-based test split and (ii) a stratified Google FAIL+FINISH sample (n = 1,000,111). Features: `plan_cpu_frac`, `plan_mem_frac`, and retry index only. No host-grid window. `machine_id` excluded. Headline metrics are ROC-AUC, PR-AUC, and lift. This is a feature-alignment diagnostic, not the classifier of Section 3.6.

**Table 8.** Restricted-feature transfer diagnostic. F1 at threshold 0.5 on Google is reported only to show that it is confounded by prevalence (Google F1 0.37 > Alibaba F1 0.09 while ROC-AUC falls). Google F1 at 0.5 is not evidence of transfer.

| Population | n | Positive rate | ROC-AUC | PR-AUC | Lift (PR-AUC / base rate) |
|------------|--:|---------------|--------:|-------:|--------------------------:|
| Alibaba time-test (primary rack) | 3,974,412 | 0.0951% | 0.6107 | 0.0991 | 104.2 |
| Google sample (FAIL+FINISH) | 1,000,111 | 18.28% | 0.5095 | 0.2101 | 1.15 |
| Google equalized prevalence | 898,158 | 9.001% | 0.5092 | 0.1128 | 1.25 |

ROC-AUC drop Alibaba \(\rightarrow\) Google: **0.101**. After downsampling Google positives to the diagnostic’s 9.001% training rate, ROC-AUC remains 0.509. The collapse is therefore not an artifact of quoting F1 at 0.5. Likely contributing causes, not fully separated here: (a) the ~109× prevalence gap between the Alibaba instance rate (0.17%) and Google FAIL+FINISH (18.26%) — the 200,000-row cap itself oversampled positives relative to the true Alibaba rate; (b) a three-feature subset with no host utilization; (c) `plan_mem_frac` lacking a shared physical divisor. The full model, with host-grid features and without the training cap, is required before a transfer claim is made or withdrawn. What this diagnostic does establish is that comparing raw `plan_cpu`, numeric `machine_id`, or F1 at 0.5 would have measured the wrong thing.

### 4.4 Classifier evaluation


### 4.5 Physical cost estimates


### 4.6 Replay and policy comparison


## 5. Discussion and Limitations

### 5.1 What the present measurements establish

The instance-level base rate (0.17%) is a genuine class-imbalance problem; beating always-predict-1 is no longer free. The causal chain is physically coherent: a doomed instance burns IT energy that is discarded; that energy converts to water through a metric whose denominator is IT energy; the counterfactual (do not admit, or kill at admission) is a scheduler action. Two of three coefficient families (PUE, WUE) carry figure-level citations to one report. Idle/peak power is a reproducible matched envelope. Google 2019 is aligned at the unit of analysis that does not make the label an accident of first-versus-last terminal.

None of that is a measured kilowatt-hour saving, and none of it is a validated water figure. The diagnostic’s Google ROC-AUC of 0.51 is a negative result on a three-feature subset, not a finding that doomed-workload detection cannot transfer. Classifier quality, lead time, and physical-cost ranges remain the quantities that test the hypothesis in Section 3.1.

The supported claim is wasted compute, with water as a downstream consequence of that waste. Infrastructure-fault language would be claiming something `machine_meta` cannot support.

### 5.2 Limitations

**Most failed instances have no measured waste window.** Of 21,780 primary-rack failures, 4,924 meet the costing rule. The time-based test split contains 204 of them. Failed rows with `end_time=0` carry a parent-task upper bound, reported separately, never mixed into measured kWh. That is sufficient for an order-of-magnitude range, not for subgroup analysis.

**No ground-truth validation is available for water.** Liter figures cannot be checked against a facility meter. Reporting ranges bounded by published coefficients is the mitigation, not a substitute for a pilot.

**Idle and peak power remain a published envelope, not a facility measurement.** The matched SPEC range (40.1–176 W idle, 241–650 W peak, n = 19) is the dominant source of width in every forthcoming kWh/liter figure. Supercloud does not close this gap: its CPU timeseries has no watt column.

**Generalization under distribution shift is not established.** The held-out rack is a replication check (same window, same hardware, near-identical marginals). The Google diagnostic shows ROC-AUC 0.51 on a restricted feature set; the cause is only partly diagnosed (prevalence, missing host features, incomparable memory fractions). The full classifier is required before this is treated as a result about shift.

**PUE and WUE are national averages, except where operator ESG points are used as a labeled scale.** A number from a named hyperscaler’s sustainability report is not interchangeable with LBNL’s U.S. average. Table 7 keeps them separate.

**This methodology does not validate results against Frontier or any specific facility.** It validates an approach on public data.

**Classifier metrics and physical-cost ranges are not yet in this draft.** Filling those cells with the Section 4.3 diagnostic, or with an unlabeled oracle over all 204 test failures, would confuse a ceiling with a deliverable.

### 5.3 Impact

The physical cost of a doomed workload is, under current monitoring practice, invisible at the moment of admission. A range-bounded estimator would give operators — and potentially external auditors — an order-of-magnitude estimate of energy and onsite water attached to failures they already log, without requiring full cooling-plant instrumentation. That is relevant to disclosure requirements around AI and cloud water and carbon reporting: a tool built on telemetry already collected lowers the barrier for operators who lack dedicated sustainability instrumentation.

What the present evidence does *not* buy: a guaranteed percentage saving; a replacement for Modelica-grade cooling optimization; a hardware-fault early-warning system; or an ESG disclosure that could be filed without a facility-specific coefficient campaign. Those distinctions matter for an industrial reader. Overstating them fails the claim more badly than leaving the cost tables blank until they are computed.

A useful split for any non-technical summary of this work is **demonstrated** (instance-level tables, freeze, cited envelopes, Google attempt alignment, diagnostic AUC 0.51 labeled as such) versus **requires a facility pilot to confirm** (any operational kWh/liter saving, any site WUE, any hardware-fault claim). Projections must not read as demonstrated results.

### 5.4 Future work

**Remaining experiments on this corpus.** Classifier evaluation on the frozen primary test set; one replication pass; Fan integral and 16-corner sweep on costing-eligible true positives; replay accumulation versus the reactive baseline, including a kill-if-\(P(\mathrm{fail}) > \tau\) policy. Scoring the Alibaba-trained full model on Google attempts uses ROC-AUC, PR-AUC, and lift. Supercloud remains a form check, not a CPU-coefficient source.

**Operator-published facility WUE.** Substituting a site WUE for the U.S. national average reframes the output; it does not provide validation ground truth.

**Facility-grade validation.** Given access to a real facility, swept coefficient ranges could be replaced with measured idle/peak power and actual WUE, allowing direct comparison between the estimator and meters. That is the only means of establishing whether the coefficient-based approach is accurate enough for operational use. A pilot would need admission-time job/instance records with outcomes; host utilization at one-minute or better; measured IT power or a calibrated per-class power model; site WUE and PUE for the same period; and a freeze rule analogous to Section 3.1 so that costing is not tuned on the facility set used to claim savings.

**Genuine hardware-fault prediction.** The present work predicts doomed workloads because the Alibaba trace contains no hardware-fault labels. A trace that records machine state transitions, or facility telemetry with maintenance records, would permit the infrastructure-level framing originally considered and since withdrawn.

**Governance and monitoring extension.** The telemetry-classification approach used here is structurally related to work on detecting AI training runs from power and network-bandwidth signals, including a classifier reported at approximately 95% accuracy distinguishing AI training from other workloads on MIT Supercloud (Hardware-Level Governance of AI Compute, 2026). That paper uses power **and** network; the Supercloud artifact used here is GPU power only. This is a plausible extension given architectural similarity between fault classification and workload-type classification. It is not a current capability.

**What no dataset can fix.** No public dataset joins fault events to measured facility water consumption. That is a property of the problem. It is why results are reported as coefficient-bounded ranges, and it remains true after any of the crossings above.

Hardware-failure corpora such as Backblaze drive statistics or the Computer Failure Data Repository were considered and rejected: they describe different failure modes with no join key to the Alibaba workload. Combining them would recreate the framing error that revision 11 in Appendix A corrected.

## 6. Conclusion


## Code and Data

- **Code:** accompanying `fpce` repository (ingestion, feature contract, coefficient loader, Google alignment, SPEC/Supercloud provenance).
- **Reproduction:** `pip install -e ".[dev]"` then `bash scripts/run_ingest.sh`. Checks: `python scripts/validate_config.py && pytest`.
- **Pinned artifacts:** `params/physical_cost.toml`, `params/feature_contract.json`, frozen split JSON, SPEC envelope parquet, quality reports.
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

## Appendix A. Revision history

This appendix documents how the present draft differs from the original proposal, why each change was made, and what remains open. Most changes were forced by measurements on ingested data. The repository was placed under version control after these revisions, so the table is reconstructed from the engineering work log rather than a commit diff.

| # | Area | Original draft | Current draft | Triggering evidence |
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
| 13 | SPEC provenance | Idle 80–220 W, peak 150–450 W, cited to a summarizer | Idle 40.1–176 W, peak 241–650 W from 19 matched systems; locked to parquet | Cited function produced 47.4–128 / 206–827 W on 12 vendor-restricted results and never divided by `identical_nodes`. |
| 14 | SPEC sample | 15 result files | 1,116 parsed of 1,156 linked results | Index page exposes 1,156 `.txt` reports. |
| 15 | `end_time=0` costing | Dropped, then at risk of invented duration | Measured window stays 0; parent-task upper bound stored separately | 6,970 of 21,780 primary failures have `end_time=0`. |
| 16 | Google unit | One row per Borg instance (first SCHEDULE, first terminal) | One row per **attempt** | 39.1% of keys had more than one terminal; first vs last swung positives 6.75% \(\rightarrow\) 0.08%. |

| Limitation | Status | Next step |
|------------|--------|-----------|
| Power coefficients unreproducible / too generic | Closed | Matched SPEC envelope, provenance test |
| Linear form untested on measured power | Closed as a form check | Supercloud GPU \(R^2 = 0.79\); coefficients do not transfer |
| No distribution-shift test | Corpus aligned; full classifier pending | Diagnostic ROC-AUC 0.51 on Google sample |
| Prevalence not comparable (~109\(\times\)) | Open (metric choice) | ROC-AUC / PR-AUC / lift; do not cite F1 at 0.5 as shift |
| Feature scales incompatible | Closed for CPU | Google `plan_cpu_frac` p50 0.01041 vs Alibaba `plan_cpu/100/96` = 0.0104. Memory open. |
| Small costing sample (204 test events) | Partially closed | Task-level upper bound + deferred replication-rack pool (5,123) + Google 1.18 M costable attempts |
| Water coefficient is a national average | Open | Operator ESG scale table exists; not validation |
| No joint fault-and-water ground truth | None available | Remains a stated limitation |

## Appendix B. LLM usage statement

Claude Opus 5.0 and Cursor Grok 4.6 were used in this project for pipeline implementation and debugging, and for drafting this technical record from repository artifacts (quality reports, coefficient files, proposal text, and source comments).

Every quantitative claim in Sections 4.1–4.3 was checked against the pinned quality reports, the coefficient file, and the frozen split. Coefficient citations were checked against the registry references. No kilowatt-hour or liter estimate was generated by a language model. Table 8 is labeled as a restricted-feature diagnostic and was not rewritten as the official classifier result.

Language-model assistance does not substitute for independent verification. A reader who distrusts AI-generated research should re-run the quality reports and tests against the pinned parquets rather than take this prose as primary evidence.
