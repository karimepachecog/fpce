# **Physical Quantification of Anomaly Impact: A Fault-Triggered Physical Cost Estimator (FPCE) for Datacenters**

*Linking doomed-workload detection to the physical cost of inaction, estimating, at instance admission, the energy and cooling water that will be wasted if a failing batch instance is allowed to run to completion.*

The proposed artifact is termed a **Fault-triggered Physical Cost Estimator (FPCE)** rather than a digital twin. In the prior work referenced below, a digital twin denotes a physics-simulated model (Modelica- or EnergyPlus-grade) validated against facility telemetry. The system proposed here is a coefficient-based estimator layered on an ML anomaly predictor; the term "digital twin" would overstate its fidelity relative to that prior work and is avoided accordingly.

## **Hypothesis**

Reactive monitoring (e.g., retry-until-success or a runtime threshold) lets a doomed batch instance occupy a machine until it fails. A predictive model can, in principle, flag the same instance at admission, while its remaining runtime is still ahead. The physical cost of that inaction (measured in kWh and liters of cooling water) is the energy the instance would burn between the decision point and its eventual failure — work that is discarded.

**Testable claim:** for a fixed anomaly class (batch-instance failure) and a fixed reactive baseline (a retry-count or runtime-threshold rule reflecting standard practice), the predictive model will produce a positive lead time on a measurable proportion of held-out failed instances, and the physical cost accumulated during that remaining runtime, computed via the translation layer described below, will be strictly greater than zero on those events.

This formulation is intentionally conservative. No specific percentage reduction is claimed, as this figure depends on the predictor's lead-time distribution, which is not known prior to training; specifying a percentage in advance would reproduce the placeholder problem identified in the previous draft under a different value. The magnitude of the effect is treated as an experimental result rather than an a priori claim; the structural claim, that earlier detection yields a measurable, non-zero physical-cost gap, is what is being tested.

No claim is made that the resulting kWh/liter figures correspond to measurements from an operating facility, as no facility access is available for validation. This limitation is addressed explicitly in the Validity and Limitations section below rather than left implicit.

## **Objective**

The MVP will:

1. Detect **batch-instance failure**, a single, unambiguous anomaly class with explicit status-code labels in the Alibaba cluster trace (`Failed` / `Interrupted` in `batch_instance`). The modelling unit is the instance, not the machine-minute: machine-minute failure is not an anomaly in this trace (~550 instance failures per machine over eight days; ~37% of 30-minute windows are positive). Instance-level failure is ~0.17% of completed instances.  
2. Predict, at instance admission (`decision_time = start_time`), whether the instance is doomed, with sufficient lead time to be measurably earlier than a reactive baseline.  
3. Translate the remaining runtime of true-positive doomed instances into an estimated physical cost (kWh and liters), using a power model grounded in prior literature rather than an unstated assumption.  
4. Report results as a range rather than a point estimate, with an explicit statement of what would be required to validate the estimate against real facility data.

## **Datasets**

* **Alibaba Cluster Trace Program v2018** (primary) — eight-day trace with machine-level utilization (`machine_usage`) and per-instance batch workload status (`batch_instance`), including explicit `Failed` labels. Data is acquired via the public Beijing OSS mirror without cloud credentials. Training uses a homogeneous 40-machine rack in failure domain 51.  
* **Alibaba cluster-trace-v2018, held-out rack** (replication check) — a second homogeneous 40-machine rack from failure domain 52, withheld until a single evaluation after model training. This is a **replication** check, not an out-of-distribution test: the rack is drawn from the same 8-day window with the same hardware specification, and its measured marginals closely match the primary rack (CPU 40.72% vs 40.22%, label rate 36.8% vs 37.6%). It establishes that results are not an artifact of one particular set of 40 machines; it does not establish generalization under distribution shift. Genuine shift testing would require a different time window or a different hardware configuration, and is identified as future work.  
* **Google Cluster Data** — not used in the present MVP. The 2011 trace (~41 GB, `gs://clusterdata-2011-2`) requires gcloud access and a questionnaire; the 2019 trace is BigQuery-only. Both are identified as future work once accessibility permits.  
* **Fan, Weber, and Barroso (2007)** — source of the linear utilization-to-power model used in the translation layer. Idle and peak power **ranges** for the sensitivity sweep are recorded in `params/physical_cost.toml`; SPECpower_ssj2008 results bound the envelope across comparable server classes but are **not** mapped to individual trace machines (hardware identity is anonymized).  
* **Lawrence Berkeley National Laboratory, 2024 U.S. Data Center Energy Usage Report** — source of the site WUE range (0.45–0.48 L/kWh, Figure 4.7) and the PUE range used for facility energy as a separate line item (~1.4 U.S. average in 2023; 1.15–1.35 in 2028 scenarios, Figure 4.6), operationalized in `params/physical_cost.toml`.  
* **The Green Grid WUE methodology** — definitional basis for the conversion: WUE = annual site water (L) / IT equipment energy (kWh) (White Paper #35, 2011). The simple onsite WUE metric is used; WUE\_source (offsite water embedded in grid electricity) is explicitly out of scope, as it would require grid-mix data not available to this project. Because the denominator is already IT energy, a cooling-share multiplier is not applied.

No dataset used here contains joint fault-and-water ground truth; no public dataset does. This is treated as a property of the problem rather than a gap specific to this project, and is addressed by reporting estimates as ranges bounded by published coefficients rather than as point measurements.

## **State of the Art**

Reactive monitoring tools (Prometheus, Nagios, ELK) trigger threshold-based remediation without accounting for the physical cost of the underlying failure. Standard efficiency reporting relies on static PUE/WUE ratios, which are known to extrapolate poorly outside the conditions under which they were derived.

Two bodies of prior work directly bound the scope of this project:

**Physics-informed digital twins for cooling optimization.** [Jadhav and Liu (2026a)](https://arxiv.org/abs/2603.01198) developed a Modelica-based digital twin of the liquid cooling plant at the Frontier exascale supercomputer, validated against a full year of ten-minute operational data. [Jadhav and Liu (2026b)](https://arxiv.org/abs/2605.15516) extended this to co-design optimization across coolant distribution unit partitions, reporting approximately 35% annual cooling energy savings; a subsequent paper extends the framework to full life-cycle optimization ([Jadhav and Liu, 2026c](https://arxiv.org/abs/2606.15408)). This line of work constitutes the most directly relevant prior art. It is also the basis for the present project's scope: it is an offline, annual-scale, energy-only optimization framework, validated against proprietary Oak Ridge National Laboratory telemetry that is not available to this project, and it does not respond to individual predicted faults in real time.

**Failure prediction and cooling fault detection as a maturing subfield.** A 2026 review in *EPJ Special Topics* and a 2026 review in *ScienceDirect* both survey AI and digital-twin approaches to cooling failure prediction, indicating that this area is sufficiently mature to be systematically reviewed; general claims of novelty in this space require specific justification. An applied study ([IRJMETS, 2025](https://www.irjmets.com/uploadedfiles/paper/issue_4_april_2025/73401/final/fin_irjmets1745228941.pdf)) demonstrates LSTM- and genetic-algorithm-based real-time WUE optimization on two years of operational Tier IV data, reducing WUE from 1.8 to 1.35 L/kWh. This establishes that real-time, water-aware control is achievable, though the approach optimizes continuously rather than attaching a cost estimate to a specific predicted fault.

No work identified in this review ties a specific predicted anomaly to a counterfactual physical cost, computed from public rather than facility-proprietary telemetry, and reported with explicit uncertainty rather than as a point estimate. This is the gap the present project addresses, a narrower and more defensible claim than general-purpose digital-twin-based sustainability optimization.

## **Methodology and ML Model**

**1\. Data pipeline.** Alibaba cluster-trace-v2018 serves as the primary data source. The prediction table is `instance_events.parquet`: one row per batch instance, labelled `failed` if `batch_instance.status` is `Failed` or `Interrupted`. `Terminated` is the negative class. Still-running (`Running` / `Ready` / `Waiting`) instances are censored and excluded from training. Failed rows with `end_time=0` keep `start_time` as a failure proxy for classification but are excluded from costing (no measurable waste window). Host utilization is resampled to a 1-minute grid per rack and joined only at timestamps ≤ `decision_time`. The primary rack (failure domain 51) is split time-wise on `start_time` (75% train / 25% test). A second rack from failure domain 52 is held out for the single replication check. Processed artifacts and a data dictionary live under `data/processed/` and `docs/data_dictionary.md`; a machine-readable feature allow/deny list lives in `params/feature_contract.json`; quality metrics are summarized in `reports/data_quality.md`.

**Physical cost coefficients** are not downloaded telemetry. They are maintained in `params/physical_cost.toml` (P\_idle, P\_peak, PUE, WUE ranges with citations). Role C (electrical engineer) loads this file via `fpce.costing.coefficients`. A cooling-share coefficient is **not** used: Green Grid WUE is already denominated in IT energy, so multiplying by a cooling share would double-count.

**2\. Machine learning model.** A gradient-boosted classifier is used in preference to a recurrent model, both for lower inference latency and because the label structure (binary fail/succeed per instance) does not require sequence modelling of the instance itself. Features are restricted to information available at admission: planned resources, retry index, task type, and a short trailing window of **host** utilization ending at `decision_time`. Post-completion instance telemetry (`cpu_avg`, `cpu_max`, `mem_avg`, `mem_max`) and identity columns (`job_name`, `task_name`) are denied. The model outputs a failure probability; lead time is the interval between that decision and when a reactive baseline (retry count or runtime versus task median) would have acted on the same instance.

Evaluation is defined explicitly, as follows:

* Precision, recall, and F1 of the classifier on held-out primary-rack instances, benchmarked against the reactive baseline and against constant classifiers (always-0 / always-1). Always-1 is no longer a strong baseline once the unit of analysis is the instance.  
* Lead-time distribution: for costing-eligible true positives, the wall-clock interval between classifier decision and baseline firing. This distribution determines the physical-cost result and is reported directly rather than compressed into a single figure.  
* A replication check, computed once on the held-out failure-domain-52 rack using the same metrics, reported as obtained regardless of outcome. This is reported as replication across machine sets, not as generalization under distribution shift (see Datasets).

**3\. Physical cost translation.** For each costing-eligible true-positive prediction, host utilization during `[decision_time, event_end)` is used to estimate IT power via the linear model of Fan, Weber, and Barroso (2007): P \= P\_idle \+ (P\_peak − P\_idle) × utilization, with P\_idle and P\_peak taken from the swept ranges in `params/physical_cost.toml` (bounded by SPECpower_ssj2008 envelope data, not per-machine mapping; combinations with P\_idle > P\_peak are dropped). IT energy is converted to liters using the LBNL site-WUE range (0.45–0.48 L/kWh) **directly** — Water = IT kWh × WUE — because WUE's denominator is IT energy (Green Grid WP#35). Facility energy is reported as a separate line item, IT kWh × PUE (LBNL 2024, 1.15–1.40).

Every output of this layer is reported as a range rather than a point estimate, as every input (idle/peak power, PUE, WUE) is itself derived from a published range rather than a direct measurement.

**4\. Simulation and interface.** A replay harness executes the trained classifier against held-out trace data in simulated real time, logs predicted-versus-threshold lead times per event, and reports the accumulated physical-cost range across the set of true-positive events. This harness produces the primary experimental result,  the lead-time distribution and corresponding cost range, rather than serving as a demonstrative interface alone.

## **Validity and Limitations**

* **Most failed instances have no measurable waste window.** Of 21,780 primary-rack failures, 4,924 have `end_time > start_time` and duration ≥ 60 s. Costing is restricted to that subset; the remainder can be classified but cannot be converted to kWh/liters without inventing a duration. The time-based test split contains 204 costing-eligible failures — enough for a range estimate, not for a finely sliced subgroup analysis.  
* **No ground-truth validation is available for the water estimates.** The resulting liter figures cannot be verified against facility measurements. This is addressed by reporting ranges bounded by published coefficients rather than single values, and by stating this limitation explicitly in any resulting write-up.  
* **Idle and peak power values are assumed rather than measured**, as trace data anonymizes hardware identity. A range of values is swept, and sensitivity of the final result to this range is reported.  
* **Generalization under distribution shift is not tested at all.** The held-out rack establishes replication across machine sets within the same window and hardware class; it is not an out-of-distribution evaluation. The result of that check is reported regardless of outcome.  
* **This methodology does not validate results against Frontier or any specific facility.** It validates an approach on public data; extension to a specific facility requires access not currently available and is identified as future work below.

## **Impact**

The physical cost of a fault is, under current monitoring practice, invisible at the moment it occurs; operators observe resource-level alerts rather than water or carbon consequences. A range-bounded, coefficient-based estimator provides operators, and potentially external auditors, with an order-of-magnitude estimate of physical cost without requiring full facility instrumentation. This is relevant to increasing disclosure requirements around AI and cloud water and carbon reporting, where a tool built on telemetry already collected lowers the barrier to reporting for operators without dedicated sustainability instrumentation.

## **Future Work**

**Google Cluster Data ingestion.** The 2011 and 2019 Google traces remain valuable for cross-format validation but require gcloud/BigQuery access and substantially larger downloads than the Alibaba mirror already in use. Replicating this pipeline on Google data is deferred until that access is available.

**Facility-grade validation.** Given access to a real facility, the swept coefficient ranges could be replaced with measured idle/peak power and actual WUE, allowing direct comparison between the estimator's output and ground truth — the only means of establishing whether the coefficient-based approach proposed here achieves sufficient accuracy for practical use.

**Governance and monitoring extension.** This extension is explicitly out of scope for the present MVP. The telemetry-classification approach used here for fault detection is structurally related to existing compute-governance work on detecting AI training runs from power and network-bandwidth signals, including a classifier reported at approximately 95% accuracy distinguishing AI training from other workloads on the MIT Supercloud Dataset ([Hardware-Level Governance of AI Compute, 2026](https://arxiv.org/abs/2604.04712)). This is noted as a plausible extension, given the architectural similarity between fault classification and workload-type classification, without constituting a claim to implement it within the present project.

## **References**

* Fan, X., Weber, W.D., Barroso, L.A. (2007). *Power Provisioning for a Warehouse-Sized Computer.* ISCA '07. https://research.google/pubs/power-provisioning-for-a-warehouse-sized-computer/  
* Jadhav, S., Liu, Z. (2026a). *Digital Twin-Based Cooling System Optimization for Data Center.* arXiv:2603.01198. https://arxiv.org/abs/2603.01198  
* Jadhav, S., Liu, Z. (2026b). *Co-Design Optimization for Data Center Cooling System via Digital Twin.* arXiv:2605.15516. https://arxiv.org/abs/2605.15516  
* Jadhav, S., Liu, Z. (2026c). *Data Center Life Cycle Co-Design Optimization.* arXiv:2606.15408. https://arxiv.org/abs/2606.15408  
* EPJ Special Topics (2026). *Artificial intelligence and digital twins for failure prediction in data center cooling systems: a comprehensive literature review (2018–2026).* https://link.springer.com/article/10.1140/epjs/s11734-026-02411-x  
* ScienceDirect (2026). *Data center cooling system fault detection and diagnosis: A comprehensive review and outlook.* https://www.sciencedirect.com/science/article/abs/pii/S0360544226019614  
* IRJMETS (2025). *Enhancing Water Sustainability in Data Centers.* https://www.irjmets.com/uploadedfiles/paper/issue\_4\_april\_2025/73401/final/fin\_irjmets1745228941.pdf  
* Shehabi, A. et al. (2024). *2024 United States Data Center Energy Usage Report.* Lawrence Berkeley National Laboratory, LBNL-2001637. https://eta-publications.lbl.gov/sites/default/files/2024-12/lbnl-2024-united-states-data-center-energy-usage-report\_1.pdf  
* The Green Grid (2011). *Water Usage Effectiveness (WUE): A Green Grid Data Center Sustainability Metric.* White Paper #35. https://www.thegreengrid.org/en/resources/library-and-tools/238-WP%2335---Water-Usage-Effectiveness-(WUE):-A-Green-Grid-Data-Center-Sustainability-Metric  
* Hardware-Level Governance of AI Compute (2026). arXiv:2604.04712. https://arxiv.org/abs/2604.04712  
* Alibaba Cluster Trace Program: https://github.com/alibaba/clusterdata  
* Google Cluster Data: https://github.com/google/cluster-data

