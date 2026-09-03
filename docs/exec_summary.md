# Executive summary

**Energy and water attributable to compute that does not complete**

When a batch job is admitted and later fails, it continues to occupy a machine until that failure occurs. During that interval, servers draw electrical power, the facility supplies overhead energy, and cooling systems withdraw water. Operational monitoring records retries and runtime alerts. Sustainability reporting records annual site ratios. Neither practice attributes a quantity of energy or water to the individual job at the point of admission. The physical cost exists; it is not assigned to the decision that incurred it.

This study provides that assignment. At admission, each job is scored for likelihood of failure. Remaining runtime is then translated, through a published power model and national efficiency ratios, into ranges of IT energy, facility energy, and onsite cooling water. The estimates are derived from public production traces and published engineering coefficients. No savings percentage was assumed in advance.

The analysis establishes three results.

**Attribution.** On the held-out evaluation sample, failures with a measured duration of at least one minute account for **3 to 10 kWh** of IT energy and **1.5 to 4.8 liters** of onsite water if allowed to run to completion. Each figure is a range across physically consistent coefficient assumptions, not a point estimate. That quantity is an upper bound: it assumes every such failure is identified and no successful job is interrupted.

**Detection at admission.** Failures occur in approximately one job per thousand. The model ranks them reliably at that rate. Among failures with a recorded end after admission, most are identified at the start of the job (median lead time of 17 seconds). Evaluated on a second set of machines of the same class, which were never used for training, ranking quality is comparable. Identification at admission is therefore reproducible on this hardware class.

**Valuation of the intervention.** Identification is not equivalent to a beneficial operating rule. Interrupting jobs flagged under the evaluation threshold would avoid nearly all of the 3–10 kWh above, and would also discard **60 to 179 kWh** of useful work by interrupting successful jobs. A reactive rule that alerts on nearly half of all jobs discards substantially more. The analysis therefore prices both the waste avoided and the work lost to error. Relative to the failures that can be costed, observed precision is approximately **1 percent**; the intervention becomes energy-neutral at approximately **15 percent**. A more stringent threshold retains nearly the same true detections while reducing incorrect interruptions from tens of thousands to three. That threshold was not selected after inspection of the test set.

The estimates are not facility meter readings. They rest on public traces (Alibaba; Google) and published coefficients. Water volumes follow national water-efficiency ranges. The traces contain no hardware-fault labels; the object of analysis is wasted compute.

Taken together, the work supplies a method for expressing unfinished compute as energy and water, for identifying likely failures at admission, and for determining whether interruption is justified once errors are included. Applied to site measurements—power, site efficiency ratios, and admission outcomes—the same method would fix the operating threshold on training data and then evaluate it, including the cost of interrupting successful work.

Supporting methods, ranges, and limitations are given in the accompanying paper.
