# Executive slides (Google Slides outline)

Audience: ExxonMobil executives. Language: English, non-technical, formal. One idea per slide. Aligns with `docs/exec_summary.md` and `What_a_Failure_Costs.md`. Do not open with the net-energy result. Do not explain why the company should care. Do not use “digital twin.”

Speaker notes are for the presenter only. Figures: `reports/figures/policy_simulation.png`, `primary_hgb_pr_curve.png`, `primary_hgb_lead_time.png`.

---

## Slide 1 — The unattributed interval

**Title:** Energy and water spent on compute that does not complete

**On slide**

- A job that later fails continues to occupy a machine until that failure occurs.
- In that interval: electrical power, facility overhead energy, cooling-water withdrawal.
- Operations record retries and runtime alerts. Sustainability reporting records annual site ratios.
- Neither attributes a kilowatt-hour or a liter to the job at the point of admission.

**Visual:** three columns — operational alerts / annual site ratios / the interval that is not assigned.

**Speaker notes:** Open with the decision, not the model. The physical cost is incurred from admission until failure. It is not billed to that job. The study assigns that cost. Do not mention cooling-plant optimization or hardware faults on this slide.

---

## Slide 2 — What the study provides

**Title:** A physical cost attached to a predicted failure, at admission

**On slide**

- Each job is scored for likelihood of failure when it is accepted.
- Remaining runtime is translated into ranges of IT energy, facility energy, and onsite cooling water.
- Estimates use public production traces and published engineering coefficients.
- No savings percentage was assumed in advance.

**Visual:** three steps — score at admission → translate remaining runtime → range of kWh and liters.

**Speaker notes:** FPCE is the accompanying estimator. Water is IT energy times a published water-efficiency ratio; facility energy is IT energy times a published power-efficiency ratio. Every output is a range. The comparison is with current practice: wait, retry, or stop after a runtime threshold.

---

## Slide 3 — Evidence

**Title:** Public traces and published coefficients

**On slide**

| Source | Use |
|--------|-----|
| Alibaba cluster trace, 2018 | Training and held-out evaluation; second rack of the same class held out |
| Google cluster data, 2019 | Cross-provider diagnostic, not a training pool |
| Published power and efficiency coefficients | Idle and peak watts; national site energy and water ratios |

- The time split is locked before model selection.
- Features available only after a job completes are excluded.

**Speaker notes:** Alibaba is the headline evaluation. The second rack is the same week and hardware class: a replication check, not a claim of generalization under shift. Google is scored on overlapping resource fractions only. Coefficients are publications, not site meters. Replication ranking: 13.0 million instances, ranking quality comparable to the primary evaluation (see appendix).

---

## Slide 4 — Attribution

**Title:** The waste, expressed as energy and water

**On slide**

On the held-out sample of failures with a measured duration of at least one minute:

| If those jobs run to completion | Range |
|--------------------------------|-------|
| IT energy | **3 to 10 kWh** |
| Onsite cooling water | **1.5 to 4.8 liters** |

An upper bound: every such failure identified, and no successful job interrupted.

**Speaker notes:** This is 204 test failures, not all 3,778 test failures. Most failures are too short to cost at one-minute host resolution. The range spans physically consistent coefficient corners. Midpoints are midpoints of an envelope, not meter readings. This quantity could not be stated from an annual site ratio alone.

---

## Slide 5 — Detection at admission

**Title:** Failures can be identified when the job is accepted

**On slide**

- Failures occur in approximately **one job per thousand**.
- Among failures with a recorded end after admission, most are identified at the start of the job.
- Median lead time: **17 seconds**.
- On a second set of machines of the same class, never used for training, ranking quality is comparable.

**Visual:** lead-time figure (`primary_hgb_lead_time.png`).

**Speaker notes:** 1,210 of 1,276 failures with a recorded end after admission. Ranking metrics on the primary held-out set: PR-AUC 0.802, ROC-AUC 0.984. Replication rack: PR-AUC 0.861, ROC-AUC 0.986. Do not mix three denominators: all test failures (3,778), those with a measurable end (1,276), and those costed at ≥60 s (204). Do not present replication precision (0.441) as an improvement on the primary test (0.141); prevalence differs.

---

## Slide 6 — Valuation of the intervention

**Title:** Identification is not equivalent to a beneficial operating rule

**On slide**

| | IT energy (range) |
|--|-------------------|
| Waste if jobs run to completion | **3.4 – 10.0 kWh** |
| Avoided if flagged jobs are interrupted | 3.3 – 9.8 kWh |
| Successful work interrupted in error (20,523 jobs) | **60 – 179 kWh** |
| **Net** | **−176 to −51 kWh** |

A reactive rule that alerts on nearly half of all jobs discards substantially more.

**Visual:** `policy_simulation.png` (avoided versus discarded, logarithmic scale).

**Speaker notes:** This is the contribution of pricing the action, not only ranking it. Incremental energy versus the reactive rule on the 204-event sample is 0.034–0.097 kWh. Interrupted successful work is not “savings.” Present the net as what the method reveals, not as a failure of the study.

---

## Slide 7 — The condition for an energy-neutral rule

**Title:** Observed precision approximately 1 percent; energy-neutral at approximately 15 percent

**On slide**

- The costing sample lasts **at least 60 seconds**. A reactive runtime rule fires at about **10 seconds**. Catching 203 of 204 of those jobs is a property of the sample, not evidence of a strong detector.
- Precision against those failures: approximately **1 percent**.
- Precision required for avoided waste to offset interrupted successful work: approximately **15 percent**.
- Incorrect interruptions last a median of **84 seconds** (90th percentile 245 seconds).

**Speaker notes:** If asked whether the reactive rule is “better”: it alerts on 46 percent of the test set; the model alerts on 0.6 percent. The reactive rule is more damaging, and still not energy-positive on this sample. One incorrect interruption of a multi-minute successful job outweighs many correct interruptions of short failures.

---

## Slide 8 — A more stringent threshold

**Title:** The sign of the net can reverse; that cutoff was not adopted on the test set

**On slide**

| Threshold | True detections (costed failures) | Incorrect interruptions | Net IT kWh |
|-----------|----------------------------------:|------------------------:|------------|
| Evaluation rule (0.9) | 197 | 20,507 | −176 to −51 |
| More stringent (0.999) | 196 | 3 | **+3.0 to +9.6** |
| Still more stringent (0.9999) | 47 | 0 | +0.6 to +1.8 |

Nearly the same true detections; three incorrect interruptions rather than twenty thousand.

The more stringent cutoff was not selected after inspection of the test set.

**Speaker notes:** Existence of an energy-positive region, not a recommended setting. A subsequent evaluation would select the threshold on training data and then measure. Do not replace 0.9 with 0.999 in any headline.

---

## Slide 9 — What this study makes possible

**Title:** Questions that were qualitative can now be stated in kilowatt-hours

**On slide**

Together, three elements that public traces and annual site ratios do not join:

1. An admission-time label that does not use information available only after the job ends.
2. A cited translation from remaining occupancy into energy and water.
3. An identity that counts interrupted successful work as a cost, not as a saving.

With those in place, an operator can ask whether interrupting a class of jobs reduces site energy **after errors are included**, and can state the precision at which that intervention would cease to be a net loss.

A site study can substitute measured power and site efficiency ratios **without changing the question**.

**Speaker notes:** This is the future-work claim from the paper. The value of the research is that the intervention is now an energy decision with a protocol, not a ranking score next to an annual ratio. Do not promise hardware-fault prediction or metered water from this corpus.

---

## Slide 10 — Scope of the numbers

**Title:** Ranges from public traces and published coefficients, not site meters

**On slide**

- Traces: Alibaba (training and evaluation); Google (diagnostic only).
- Water volumes follow national water-efficiency ranges.
- No public dataset joins a failed job to measured facility water.
- The traces contain no hardware-fault labels. The object of analysis is wasted compute.

**Speaker notes:** State scope calmly. It is not a list of apologies. Facility meters would replace the coefficient envelope; they would not replace the attribution, detection, and valuation structure.

---

## Slide 11 — Subsequent evaluation

**Title:** The same method, with site measurements

**On slide**

Given admission outcomes, host utilization at one-minute resolution or better, measured IT power or a calibrated power model, and site energy and water ratios for the same period:

1. Select the operating threshold on training data.
2. Replace published envelopes with site idle power, peak power, and site efficiency ratios.
3. Evaluate net energy and water on a holdout, including interrupted successful work.

**Speaker notes:** Google transfer of the twelve-feature model remains open; a three-feature diagnostic does not transfer (ROC-AUC 0.51). Do not propose AI-compute governance or Supercloud as current capability. Supercloud was a form check on the linear power model only.

---

## Slide 12 — Close

**Title:** Unfinished compute, assigned as energy and water

**On slide**

- The physical cost of compute that does not complete can be attributed at admission, as a range.
- Failures can be identified at admission at a rate of about one in a thousand, and that ranking reproduces on held-out machines of the same class.
- Whether interruption is justified is now a priced question. Under the evaluation rule, errors dominate the waste avoided. The precision at which that would change is approximately 15 percent.

Supporting methods, ranges, and limitations are in the accompanying paper.

**Speaker notes:** Thank them. If time remains, Table 11 and Table 12 from the paper support slides 4 and 6. Do not introduce additional models. The selected classifier is histogram gradient boosting; other boosting experiments were not selected.

---

## Appendix (only if asked)

- Selected model: histogram gradient boosting, threshold 0.9, selected from the test precision/recall grid (not pre-registered). Ranking metrics do not use that threshold.
- Primary held-out classification: 3,974,412 completed instances; PR-AUC 0.802; ROC-AUC 0.984; at 0.9, precision 0.141, recall 0.889, 20,523 false positives.
- Replication rack (never used for training): 13,027,395 instances; ROC-AUC 0.986; PR-AUC 0.861; at 0.9, precision 0.441, recall 0.907. Whole-rack prevalence 0.170% versus 0.095% on the primary time-split test; precision is not directly comparable. Ranking is.
- Replication costing: 5,123 costing-eligible failures → 81–244 IT kWh, 37–117 L (upper bound). The model flags 5,099 of them; interrupted successful work on that rack is not accumulated in that table.
- Google diagnostic (three overlapping features): sample \(n = 1{,}000{,}111\), ROC-AUC 0.5095; full table \(n = 18{,}591{,}767\), ROC-AUC 0.5086. No transfer claim.
- Independent costing of the same 204 host-grid windows recovers the 3.39–9.97 kWh upper bound.
