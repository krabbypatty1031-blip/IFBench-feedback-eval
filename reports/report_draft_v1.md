# Verifier-Guided Revision for Instruction Following: Comparing Feedback Granularity on IFBench

Zhang Yanxiao 23222069

## Abstract

Nowadays, Large Language models can answer a question while disobeying its output constraints, and revision also can introduce new violations. We evaluated whether executable verifier feedback supported more reliable revision on IFBench. We have conducted and compared four branches: an initial answer (B0), unaided self-revision (B1), revision with overall verifier feedback (B2), and revision with per-constraint feedback (B3) by using two models named gpt-4.1 and gpt-4o. Each revision branch started independently from the same initial answer. On the 260 test prompts, B2 increased paired strict prompt-level accuracy by 9.69 and 10.08 percentage points, respectively. Both differences remained significant in post-hoc cluster sign-flip tests with Holm correction. B1 introduced more regressions among initially passing answers than B2. We did not detect a B2–B3 difference. A content review covered all 2080 answers from the 260 test prompts and identified cases where rule compliance did not imply adequate content. These results support overall verifier feedback in this setting, subject to gateway uncertainty and post-run recovery decisions.

## 1. Introduction

Instruction following requires a model to satisfy the requested task and the constraints imposed on its answer. A mathematical explanation can be correct yet exceed a word limit; a response can satisfy a lexical rule while offering an inadequate explanation. These properties need separate evaluation. IFEval introduced executable checks for verifiable instructions [1], and IFBench extended this approach to unfamiliar constraint types [2]. Such checks make it possible to inspect individual failures and to use their outcomes as feedback during generation.

Revision creates a second opportunity to satisfy a constraint, but it also creates an opportunity to damage an answer that already passes. A strategy that repeatedly asks a model to check its work therefore needs evaluation on initially successful as well as unsuccessful answers. Reporting only repaired failures would omit this cost in answer quality. Our experiment retained both groups and measured the transitions from failure to success and from success to failure.

The central question was whether a short verifier status could guide revision as effectively as a detailed list of constraint outcomes. We compared three independent revision branches against a shared initial answer: self-revision without an external result, revision with an overall strict pass/fail result, and revision with a strict result for each constraint. We used the HKBU gateway identifiers gpt-4.1 and gpt-4o to examine whether the observed pattern occurred in both available deployments. We did not treat these identifiers as independently verified official model snapshots.

Our first contribution is a paired implementation that preserved original prompts, branch independence, verifier outcomes, and recoverable generation records. Our second contribution is an analysis separating net accuracy changes from repairs and regressions. Our third contribution is a content audit connecting rule outcomes to author-confirmed completion labels. Together, these contributions address whether feedback helps, how it helps, and what the verifier score leaves unmeasured.

The results favor overall verifier feedback as a practical choice for this experiment. B2 increased paired strict accuracy by 9.69 percentage points for gpt-4.1 and 10.08 points for gpt-4o. Its regression rates were 5.1% and 6.8%, compared with 13.8% and 10.8% for B1. However, the evidence does not establish that detailed feedback is inferior: the B2–B3 comparisons did not reach statistical significance. We therefore interpret the results through measured transitions and uncertainty rather than a universal ranking of feedback methods.

## 2. Background Survey and Related Work

### 2.1 Evaluating instruction following

A response can address the user's question and still violate a specified output constraint. For example, an answer may provide a relevant explanation while exceeding a word limit. To evaluate such requirements consistently, IFEval introduced prompts with instructions that can be checked by programs, including word-count and keyword requirements [1]. An automatic verifier makes the rule decision reproducible for a given response and implementation. It does not, however, establish whether the response is accurate, sufficiently detailed, or otherwise useful.

IFBench extended the evaluation of verifiable instruction following to 58 out-of-domain constraint types [2]. Its focus on unfamiliar constraints makes it suitable for examining whether models can follow requirements beyond a small set of familiar benchmark rules. We used the IFBench prompts and its official strict and loose evaluators. The dataset version selected for this project contains 300 prompts, of which we assigned 260 to the test split. Our goal was not to train a model on new constraints; we examined whether feedback from the existing verifier could help a model revise an answer at inference time.

### 2.2 Revising answers with feedback

A model can use feedback generated by itself to revise an initial answer. Self-Refine follows this approach: a model produces an answer, generates feedback on that answer, and uses the feedback to refine it over successive iterations [3]. Our B1 condition provides a simpler comparison. It asks the model to check its initial answer once and return a revision; it does not generate a separately recorded critique or repeat the refinement process. B1 therefore tests a single self-revision request rather than reproducing Self-Refine.

Feedback can also come from outside the model. CRITIC uses external tools to check aspects of an initial output and then uses their feedback for revision [5]. In our experiment, the external tool was the fixed IFBench verifier. B2 received an overall pass-or-fail result, whereas B3 received a result for each constraint together with the corresponding original requirement. These conditions share the idea of tool-informed revision with CRITIC, but they do not reproduce its full tool-interaction framework. In particular, the verifier did not supply a corrected answer or explain how to repair a failure.

Reflexion illustrates a broader use of feedback across attempts: an agent records verbal reflections in memory and uses them in later trials [4]. We included it as background for feedback-driven improvement, not as an implemented method. Our experiment used no persistent reflection memory; each revision branch started from the same initial answer and ran once.

### 2.3 Research question

The preceding work motivates comparing self-generated and external feedback, while leaving our specific single-revision setting to be measured. A verifier can report that an answer failed without identifying the failed requirement, or it can report each constraint separately. The more detailed message gives the model more information, but neither type supplies a solution. The model must still make a revision that satisfies the requirement.

Revision can also change an answer that already passed. Measuring only how often a failed answer is repaired would therefore miss regressions introduced by the same procedure. We compare B1, B2, and B3 against the shared initial answer for both initially passing and failing prompts. We also review a sample of content, because passing an executable constraint does not establish that the underlying request was adequately answered. This design evaluates feedback for one revision on IFBench; it does not establish a general ranking of the broader methods discussed above.

## 3. Methodology

### 3.1 Shared initial answers and independent revisions

We used a shared initial answer to control the starting point of each within-model comparison. For each test prompt and model, B0 consisted of a single generation from the original IFBench user message without an additional answer-format wrapper. We then constructed B1, B2, and B3 as separate conversation branches containing that original message, the B0 assistant answer, and a revision instruction. No branch received another branch's response. Each branch requested one final revision, without a repeated verifier-guided loop.

We revised initially passing answers as well as failing answers because both transitions were part of the research question. Restricting revision to failures would have prevented measurement of damage to correct outputs. The implementation stored prompt identities, model identifiers, conditions, generation status, responses, and scores separately. This preserved the relationship between a response and its experimental condition and supported later paired analysis.

### 3.2 Feedback conditions

B1 asked the model to check the original output constraints itself. Its additional instruction was: “Check the original output constraints yourself. Do not assume they were already satisfied.” This condition supplied no external result. It provided a control for the effect of an additional generation opportunity accompanied by a generic review request.

B2 supplied the overall strict verifier outcome. A failed answer received: “Verifier (strict): FAILED. At least one output constraint was not satisfied.” A passing answer received: “Verifier (strict): PASSED all output constraints.” This feedback indicated whether revision was needed according to the verifier, but did not identify a failing constraint or prescribe a correction.

B3 supplied strict outcomes for individual constraints in their original order. Each line contained PASS or FAIL followed by the corresponding requirement extracted from the prompt. The mapping code matched natural-language requirements rather than exposing internal verifier parameters as an explanation. When a requirement could not be mapped reliably or verification failed, the implementation recorded the problem instead of inventing a result.

### 3.3 Prompt design and interpretation

The revision prompts emphasized preserving the original task and its constraints. All branches shared an instruction to review the previous answer against the original user message and to return only the final answer. This avoided adding explanatory headings or wrappers that could themselves violate the task. Appendix C records the template components and the message order.

B2 and B3 explicitly allowed the model to retain an answer when the verifier reported success. This instruction was a deliberate part of the feedback design, but it also limits causal interpretation. B1 did not receive the same permission conditioned on a verified pass. A reduction in regressions therefore reflects the combined status-and-prompt intervention, rather than an isolated effect of transmitting a single bit of information. No additional ablation separated these components.

The verifier measured the same strict properties used to generate feedback and to calculate the primary outcome. This made the intervention targeted and reproducible, but it also meant that the final score measured performance of a model-plus-verifier system on those properties. We included loose scores and a separate content review to contextualize that score, without presenting either as a fully independent replacement for strict evaluation.

**Figure 1 placeholder.** Original prompt → B0 → three independent revision branches. B1 receives a review request; B2 receives overall strict status; B3 receives per-constraint status and requirements. Each answer receives strict/loose scoring, and the fixed review subset receives content labels. The diagram must show that B1–B3 do not feed into each other.

## 4. Experimental Setup

### 4.1 Data and protocol freeze

We used the IFBench dataset at repository revision `cd189a9c2889526d8af901d2103fd3e1ffcea6df`. The selected JSONL file had SHA-256 `d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a`. We retained the original prompt text because preprocessing could alter the constraints being evaluated. The project identified another dataset copy with trailing-space differences, so all experimental processing used the selected file.

The split contained 40 development prompts and 260 test prompts. The grouping process kept identified related tasks together and used seed 42. The recorded groups were incomplete evidence of task relatedness, not proof of independence from model training data. We used the frozen test identities for the final comparison. The original configuration and prompt hashes were saved before the formal run; this was a local protocol freeze, not a public preregistration.

### 4.2 Model calls and recovery

The formal run used the HKBU GenAI gateway identifiers gpt-4.1 and gpt-4o through the project's Azure-shaped client. The frozen configuration specified temperature 0, seed 42, and an initial output limit of 4096 tokens. The configuration also contained a disabled-thinking setting, but client-side compatibility handling meant that configuration fields were not automatically evidence of provider-side support. In particular, deterministic settings did not establish reproducibility of an opaque gateway deployment.

The final analysis contained 2080 expected identities: 260 prompts, two models, and four conditions. The initial records included 1891 successes, 53 empty responses, and 136 errors. Subsequent recovery used a 16384-token setting and a 32768-token probe. The final merging policy retained successful original responses and replaced empty or error identities when a later source supplied a scorable answer. It replaced 169 identities and produced 2059 successes, 11 empty responses, and 10 errors. We used this final merged set throughout the report.

### 4.3 Metrics, uncertainty, and content review

Strict prompt-level accuracy was the primary metric: every constraint in a prompt had to pass. Loose prompt-level accuracy used the official relaxed evaluation. Constraint-level accuracy counted individual constraints rather than prompts. Repair rate divided failure-to-pass transitions by paired B0 failures; regression rate divided pass-to-failure transitions by paired B0 passes. These denominators differ and cannot be combined as percentages without accounting for their sizes.

We calculated paired differences using responses available in both compared conditions. Existing 95% percentile intervals used 1000 bootstrap resamples, seed 42, and identified related-task groups as clusters. Additional post-hoc exact cluster sign-flip tests used the absolute sum of paired differences. Holm correction covered six baseline comparisons jointly and four exploratory comparisons separately. The tests assumed exchangeable condition labels within clusters under the null; Appendix A reports every test. These p-values and the bootstrap intervals came from different procedures.

The content review covered every test prompt across both models and all conditions, yielding 2080 answers. Labels were assigned by a single rater and represented complete, partial, incomplete, or unjudgeable task completion. This process did not provide independent repeated ratings or a comprehensive factual audit.

## 5. Results

### 5.1 Main accuracy results

B2 has the highest observed strict prompt-level accuracy for both gateway models in Table I. For gpt-4.1, B0 passes 138 of 259 scorable prompts and B2 passes 163 of 258. For gpt-4o, the corresponding counts are 148 of 259 and 174 of 258. The changing denominators reflect missing outputs, so the paired effects below are more appropriate comparisons than subtracting rounded percentages in the table.

**Table I. Prompt-level accuracy on the final merged records. Bold identifies the highest observed accuracy within each model, not a significance claim.**

| Model | Condition | Scorable / 260 | Strict pass | Loose pass |
|---|---|---:|---:|---:|
| gpt-4.1 | B0 | 259 | 138/259 (53.3%) | 155/259 (59.8%) |
| gpt-4.1 | B1 | 258 | 146/258 (56.6%) | 163/258 (63.2%) |
| gpt-4.1 | B2 | 258 | **163/258 (63.2%)** | **174/258 (67.4%)** |
| gpt-4.1 | B3 | 257 | 153/257 (59.5%) | 167/257 (65.0%) |
| gpt-4o | B0 | 259 | 148/259 (57.1%) | 163/259 (62.9%) |
| gpt-4o | B1 | 255 | 157/255 (61.6%) | 169/255 (66.3%) |
| gpt-4o | B2 | 258 | **174/258 (67.4%)** | **184/258 (71.3%)** |
| gpt-4o | B3 | 255 | 167/255 (65.5%) | 178/255 (69.8%) |

### 5.2 Paired effects and statistical comparisons

B2 increases paired strict accuracy by 9.69 percentage points for gpt-4.1 and 10.08 points for gpt-4o. Their unadjusted bootstrap intervals are [5.06, 14.07] and [5.40, 15.30] points. The corresponding Holm-adjusted p-values are 0.00042 and 0.00078 in the six-comparison baseline family. B3 also increases paired accuracy, by 5.84 and 7.84 points, with adjusted p-values of 0.03202 and 0.00329.

B1 has smaller paired changes whose uncertainty includes no improvement. Its effects are 3.10 points for gpt-4.1 and 3.53 points for gpt-4o, with bootstrap intervals of [−1.95, 8.17] and [−1.56, 8.24]. Both adjusted p-values are 0.42205. These observations do not establish that self-revision never helps; they show that this experiment did not detect a net benefit after the stated correction.

The exploratory comparisons provide qualified support for B2 over B1. B2 increases paired accuracy relative to B1 by 6.59 points for gpt-4.1 and 6.67 points for gpt-4o. After correction within the four exploratory comparisons, their p-values are 0.03790 and 0.05587. We therefore do not describe both differences as statistically significant. B2–B3 differences of 3.89 and 2.75 points have adjusted p-values of 0.28661 and 0.31050.

**Figure 2 placeholder.** Paired B1/B2/B3 minus B0 effects by model, with unadjusted 95% cluster bootstrap intervals. The caption will distinguish these intervals from the Holm-adjusted tests in Appendix A.

### 5.3 Missing-output sensitivity

The positive B2–B0 point estimates persist under the three missing-output treatments in Appendix B. For gpt-4.1, available pairs, complete four-condition prompts, and missing-as-failure yield 9.69, 9.73, and 9.62 points. For gpt-4o, they yield 10.08, 10.63, and 10.00 points. All other reported contrasts also retain their point-estimate direction. These checks examine the final merged set; they do not undo the post-run recovery decisions or prove that missingness was random. We did not repeat the corrected significance tests for every sensitivity treatment.

## 6. Analysis and Discussion

### 6.1 Net gains reflect both repair and preservation

B1's repairs were partly offset by damage to initially passing answers. For gpt-4.1, B1 repaired 27 of 120 paired failures but regressed on 19 of 138 paired successes. B2 repaired 32 of 120 failures and regressed on 7 of 138 successes. The resulting net gains were eight passing prompts for B1 and 25 for B2. This decomposition explains why an additional revision opportunity alone produced a smaller net change than verifier-guided revision in this model.

The gpt-4o transitions show the same qualitative pattern with different denominators. B1 repaired 25/107 failures and regressed on 16/148 successes; B2 repaired 36/110 failures and regressed on 10/148 successes. The paired sets differ because some generations were missing, so the repair counts are not a direct matched comparison between B1 and B2. Nevertheless, the measured regression rates were 10.8% for B1 and 6.8% for B2, while the corresponding repair rates were 23.4% and 32.7%.

A plausible explanation is that explicit success feedback discouraged unnecessary changes, but the experiment did not isolate that mechanism. B2 and B3 combined verifier status with permission to leave successful answers unchanged. B1 instead asked the model not to assume satisfaction. The observed transitions support the practical value of the whole B2 intervention. A separate prompt ablation would be required to distinguish the value of the status signal from the value of the preservation instruction.

### 6.2 Detailed feedback did not establish an additional benefit

The B2–B3 comparisons leave substantial uncertainty about their relative effect. Their unadjusted intervals are [−0.78, 8.20] points for gpt-4.1 and [−1.95, 7.45] for gpt-4o. These intervals include zero and effects favoring B2, rather than demonstrating equivalent performance. Without a predefined equivalence margin or a prospective power analysis, we cannot attribute the nonsignificant results solely to insufficient sample size.

B2 is a reasonable practical default because its feedback construction is simpler and B3 did not demonstrate an additional accuracy benefit. This is a design choice supported by the observed comparison, not proof that detailed feedback is harmful. B3 identifies requirements but still leaves the model responsible for satisfying them. Our case material includes a B3 response that copied verifier PASS statements instead of completing the task, illustrating one failure mode without establishing its prevalence.

### 6.3 Failures differ across constraint categories

Word-level constraints remained difficult even with overall feedback. For gpt-4.1, the words category contained 46 failures among 82 evaluated constraints in B0 and 39/82 in B2. For gpt-4o, the corresponding counts were 41/82 and 35/82. A failure message can signal that an answer needs changing without providing the word-level construction needed to satisfy the rule. The category totals support persistent difficulty, but do not identify a single cognitive cause shared by all word constraints.

Some categories showed larger descriptive reductions in failure counts. The gpt-4o count category changed from 14/55 failures in B0 to 7/55 in B2. The gpt-4.1 format category changed from 32/85 to 22/85. Ratio constraints changed from 18/37 to 12/36 for gpt-4.1 and from 19/37 to 12/36 for gpt-4o. Different denominators and task mixtures prevent treating these changes as controlled comparisons between categories.

Small category sizes limit the resolution of this analysis. The custom and repeat categories contained approximately ten and nine evaluated constraints per condition, respectively. Individual prompts therefore have a substantial effect on their rates. Figure 3 will show failures and denominators rather than a ranking that hides sample size. The categories follow the dataset's instruction-ID prefixes; they are descriptive groupings, and multiple constraints from one prompt are not independent observations.

**Figure 3 placeholder.** Strict failure rates by instruction-ID prefix, model, and condition, labeled with failure counts and denominators. No category-level significance stars will be shown.

### 6.4 Content completion and rule compliance diverge

The reviewed labels show a completion ceiling rather than evidence that revision cannot damage content. Across all 2080 identities, 2033 answers are complete, nine partial, 17 incomplete, and 21 unjudgeable. B0–B3 have 512, 508, 510, and 503 complete answers out of 520 identities per condition. The 2080 answers arise from 260 repeated prompts, not 2080 independent tasks. The labels are also coarse: an answer can change in clarity or factual precision without crossing a completion category.

Rule outcomes and content labels disagree in both directions. Of the 2033 complete answers, 806 fail strict evaluation. Conversely, seven partial answers and 12 incomplete answers pass strict evaluation. Incomplete cases include verifier PASS dumps and copy-span replies that satisfy a constraint without doing the original task. These disagreements show why neither the overall completion count nor the strict score should stand in for a broad quality assessment.

## 7. Case Study

### 7.1 Repair without changing the mathematical conclusion

Item 173 shows a successful rule repair while retaining the substantive mathematical answer. The prompt asks whether pointwise convergence of polynomials on [0,1] implies uniform convergence and requires stop words to occupy no more than 20% of the response. The gpt-4.1 B0 answer fails strict evaluation. B2 receives the overall failure message and passes. The original responses are reproduced below, without rewriting their mathematical content.

**B0, strict FAIL:**

> No. Let \(B_n(x)=x^n\). For each \(x<1\), \(B_n(x)\to0\), while \(B_n(1)=1\). Thus the pointwise limit is \(f(x)=0\) for \(x<1\) and \(f(1)=1\). However, \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\) for every \(n\), so convergence is not uniform.

**B2, strict PASS:**

> No. Choose \(B_n(x)=x^n\). Pointwise limit: \(f(x)=0\) for \(x<1\), and \(f(1)=1\). Yet \(\sup_{x\in[0,1]}|B_n(x)-f(x)|=1\) for every \(n\). Therefore, convergence is not uniform.

Both answers use the same counterexample and reach the same conclusion. The revision changes the surface expression enough to satisfy the checker. This case supports a narrow claim: overall feedback can accompany a successful lexical repair without replacing the central reasoning. It does not show that shortening responses generally preserves content, and this prompt was not assigned a content-review label in the fixed sample.

### 7.2 Self-revision introduces a span error

Item 287 demonstrates regression on an initially passing output. The original message begins “how to find what value in a quadratic function lands on a certain point.” It then requests the span between word indices 10 and 12, split by whitespace. The gpt-4.1 B0 response is exactly `on a certain`, which passes the official verifier. B1 changes it to `lands on a`, which fails. Both outputs are preserved exactly in the linked records.

The changed span moves one word earlier in the original text. An indexing interpretation is therefore a plausible explanation, but the answer record does not expose the model's internal reason for changing it. The observable result is sufficient for the regression claim: asking for another self-check changed an accepted extraction into a rejected one. This case illustrates why the evaluation retained originally passing answers.

### 7.3 A rule-passing answer remains incomplete in depth

Item 247 separates lexical compliance from explanatory adequacy. The original task asks what Baudrillard means while imposing prime-number word lengths. The gpt-4o B2 response passes strict evaluation:

> Baudrillard: signs replace truth; media craft myths; false tales claim truth.

The author-confirmed content label is partial because the sentence supplies a compressed characterization without developing the explanation. This judgment involves an interpretation of adequate depth, unlike the executable lexical check. The case therefore illustrates the boundary of the verifier rather than a verifier implementation error. It also shows why optimizing a constrained response toward a terse formulation can leave the underlying request under-addressed.

**Figure 4 placeholder.** Three case panels containing the original requirement, exact response excerpts, feedback where applicable, and the verified outcome. Case selection was purposive and is not an estimate of occurrence frequency.

## 8. Limitations and Protocol Deviations

The gateway identifiers do not establish the underlying official model versions. The records report behavior involving reasoning-token consumption despite configurations intended for ordinary answer generation. We cannot verify the deployment mapping, provider-side parameter handling, or correspondence to a public model snapshot. Results therefore apply to the named HKBU gateway deployments observed in this run, not to a definitive comparison of official gpt-4.1 and gpt-4o releases.

Generation recovery changed the conditions under which some final answers were produced. The initial 4096-token run recorded empty bodies and downstream errors; the run notes associate many empty initial answers with reasoning-token usage and describe later revisions failing when an assistant message was empty. Recovery used larger limits and additional attempts beyond the originally frozen retry procedure. Consequently, the final set combines generation settings rather than representing a single uniform invocation policy.

The merging rule was chosen after the formal results were available. It preserved successful original identities and replaced missing or error identities with recoveries, rather than selecting between competing successful answers by score. Nevertheless, post-run selection of this rule and the extra opportunities to obtain an answer affect interpretation. The 169 replacements are output identities, not 169 independent prompts. Our missing-output sensitivity analysis concerns the merged data and does not remove this deviation.

The project changed interfaces during development and later removed cost as a research objective. Development materials describe an earlier DeepSeek-compatible interface; the formal comparison used the HKBU gateway. The report does not pool those development generations with the formal model comparison. TODO: confirm the precise interface-transition chronology against the development run records before submission. Gateway monetary costs remain unknown, and the study does not support a claim of financial efficiency. Removing cost from the objective also did not erase the historical generation limits and retry settings.

Content auditing used a single rater over all 2080 test identities. There was no independent second rating, so we cannot estimate inter-rater agreement. The review packages hid model and condition identities. We therefore call the labels single-rater content labels rather than claiming an independently blinded human gold standard.

Statistical inference is conditional on the sampled prompts and the available dependence model. The grouping file records identified related tasks, but cannot rule out additional dependencies. The supplementary tests and correction families were specified after results existed. They control multiplicity within the stated families under the test assumptions, not across every exploratory decision in the project. We did not perform repeated generations, an equivalence test, or a prospective power calculation. Public benchmark contamination is also unknown. These restrictions prevent claims about deployment-wide reliability or the absence of content harm.

## 9. Conclusion

Overall verifier feedback produced a paired strict-accuracy increase of 9.69 percentage points for gpt-4.1 and 10.08 points for gpt-4o in the final merged IFBench experiment. Both comparisons remained significant under the stated post-hoc Holm correction. The transition counts connect the net gains to repairs and fewer regressions than unaided self-revision. Detailed feedback did not demonstrate an additional benefit, although the experiment does not establish equivalence between B2 and B3. Content labels and examples show that rule compliance remains distinct from completing the underlying request. A useful next experiment would revise only initially failing answers and separately ablate the instruction permitting unchanged output. A second direction is independent, more detailed content review with more than one rater. Neither extension was performed here. The present evidence supports a simple verifier-guided revision system while retaining the gateway and protocol limitations needed to interpret its gains.

## Author Contributions

[Member A]: TODO — specify actual responsibilities and the corresponding implementation, experiment, analysis, or writing deliverables.

[Member B]: TODO — specify actual responsibilities. Remove this entry if there was no second contributor. Do not infer contributions from placeholder order.

## GenAI Use Declaration

We used OpenAI Codex to assist with analysis scripting and preparation of this report draft. AI-assisted content labels were individually reviewed and confirmed by an author; the initial labeling tool is TODO. The authors will verify the final text, citations, computations, and quoted outputs before submission; final manuscript review is pending.

## References

[1] J. Zhou et al., “Instruction-following evaluation for large language models,” arXiv:2311.07911, 2023. [Online]. Available: https://arxiv.org/abs/2311.07911

[2] “Generalizing verifiable instruction following,” arXiv:2507.02833, 2025. [Online]. Available: https://arxiv.org/abs/2507.02833. TODO: verify and insert the author list and final publication metadata.

[3] A. Madaan et al., “Self-Refine: Iterative refinement with self-feedback,” in Advances in Neural Information Processing Systems, 2023. [Online]. Available: https://arxiv.org/abs/2303.17651

[4] N. Shinn et al., “Reflexion: Language agents with verbal reinforcement learning,” in Advances in Neural Information Processing Systems, 2023. [Online]. Available: https://arxiv.org/abs/2303.11366

[5] Z. Gou et al., “CRITIC: Large language models can self-correct with tool-interactive critiquing,” in International Conference on Learning Representations, 2024. [Online]. Available: https://arxiv.org/abs/2305.11738

## Appendix A. Complete post-hoc significance tests

The two-sided exact cluster sign-flip test uses the absolute sum of within-prompt binary differences. All differences in a known related-task cluster are sign-flipped together. Dynamic programming enumerates the distribution exactly; no Monte Carlo seed is needed. Holm correction is applied independently within the six baseline comparisons and four exploratory comparisons. Existing bootstrap intervals remain unadjusted.


Table II-A. Complete significance results.

| family | model | contrast | n | delta_pp | p | holm_p |
|---|---|---|---|---|---|---|
| baseline_six | gpt-4.1 | B1-B0 | 258 | 3.10 | 0.30200 | 0.42205 |
| baseline_six | gpt-4.1 | B2-B0 | 258 | 9.69 | 0.00007 | 0.00042 |
| baseline_six | gpt-4.1 | B3-B0 | 257 | 5.84 | 0.01067 | 0.03202 |
| baseline_six | gpt-4o | B1-B0 | 255 | 3.53 | 0.21102 | 0.42205 |
| baseline_six | gpt-4o | B2-B0 | 258 | 10.08 | 0.00016 | 0.00078 |
| baseline_six | gpt-4o | B3-B0 | 255 | 7.84 | 0.00082 | 0.00329 |
| exploratory_four | gpt-4.1 | B2-B1 | 258 | 6.59 | 0.00948 | 0.03790 |
| exploratory_four | gpt-4.1 | B2-B3 | 257 | 3.89 | 0.14331 | 0.28661 |
| exploratory_four | gpt-4o | B2-B1 | 255 | 6.67 | 0.01862 | 0.05587 |
| exploratory_four | gpt-4o | B2-B3 | 255 | 2.75 | 0.31050 | 0.31050 |

## Appendix B. Missing-output sensitivity

Available uses responses present in both conditions. Complete-four restricts each model to prompts with all four scorable responses. Missing-fail treats every unavailable response as a failure for this sensitivity calculation only.

Table II-B. Missing-output sensitivity; differences in percentage points.

| model | contrast | mode | n | delta_pp |
|---|---|---|---|---|
| gpt-4.1 | B1-B0 | available | 258 | 3.10 |
| gpt-4.1 | B1-B0 | complete_four | 257 | 3.11 |
| gpt-4.1 | B1-B0 | missing_fail | 260 | 3.08 |
| gpt-4.1 | B2-B0 | available | 258 | 9.69 |
| gpt-4.1 | B2-B0 | complete_four | 257 | 9.73 |
| gpt-4.1 | B2-B0 | missing_fail | 260 | 9.62 |
| gpt-4.1 | B3-B0 | available | 257 | 5.84 |
| gpt-4.1 | B3-B0 | complete_four | 257 | 5.84 |
| gpt-4.1 | B3-B0 | missing_fail | 260 | 5.77 |
| gpt-4.1 | B2-B1 | available | 258 | 6.59 |
| gpt-4.1 | B2-B1 | complete_four | 257 | 6.61 |
| gpt-4.1 | B2-B1 | missing_fail | 260 | 6.54 |
| gpt-4.1 | B2-B3 | available | 257 | 3.89 |
| gpt-4.1 | B2-B3 | complete_four | 257 | 3.89 |
| gpt-4.1 | B2-B3 | missing_fail | 260 | 3.85 |
| gpt-4o | B1-B0 | available | 255 | 3.53 |
| gpt-4o | B1-B0 | complete_four | 254 | 3.94 |
| gpt-4o | B1-B0 | missing_fail | 260 | 3.46 |
| gpt-4o | B2-B0 | available | 258 | 10.08 |
| gpt-4o | B2-B0 | complete_four | 254 | 10.63 |
| gpt-4o | B2-B0 | missing_fail | 260 | 10.00 |
| gpt-4o | B3-B0 | available | 255 | 7.84 |
| gpt-4o | B3-B0 | complete_four | 254 | 7.87 |
| gpt-4o | B3-B0 | missing_fail | 260 | 7.31 |
| gpt-4o | B2-B1 | available | 255 | 6.67 |
| gpt-4o | B2-B1 | complete_four | 254 | 6.69 |
| gpt-4o | B2-B1 | missing_fail | 260 | 6.54 |
| gpt-4o | B2-B3 | available | 255 | 2.75 |
| gpt-4o | B2-B3 | complete_four | 254 | 2.76 |
| gpt-4o | B2-B3 | missing_fail | 260 | 2.69 |

## Appendix C. Full revision prompt templates

B0 used the unmodified dataset prompt. Each revision used the original user prompt, the B0 assistant answer, and the following final user message components, separated by blank lines. No additional system message was introduced by this template.

**revision_shared**

```text
Please review your previous answer against the original user message. Keep the original task and every output constraint from that message.
```

**b1_increment**

```text
Check the original output constraints yourself. Do not assume they were already satisfied.
```

**allow_unchanged_if_passed**

```text
The previous answer already passed the output constraints. You may leave the answer unchanged.
```

**output_rule**

```text
Return only the final answer. Do not describe the revision.
```

B1 concatenated revision_shared, b1_increment, and output_rule. B2/B3 concatenated revision_shared, the verifier message below, allow_unchanged_if_passed only when B0 passed, and output_rule.

```text
B2 pass: Verifier (strict): PASSED all output constraints.
B2 fail: Verifier (strict): FAILED. At least one output constraint was not satisfied.

B3 header: Verifier (strict), one line per constraint, in listed order:
B3 line format: {one-based index}. {PASS or FAIL}: {requirement copied from prompt}
```

The labels “B2 pass,” “B2 fail,” and “B3 header” above describe the template and were not sent to the model.

## Appendix D. Numerical and case provenance

Paths below are relative to the repository root. Generated tables preserve the source precision; prose rounds percentages to one decimal and paired differences to two decimals.

| Evidence | Source file / field |
|---|---|
| Dataset size, split, seed, models and initial settings | `configs/feedback_eval/official.yaml`; `split.json`; `runs/feedback_eval/official/freeze.json` |
| Main strict/loose rates and repair/regression counts | `outputs/report_materials/results.json`, tables and summary |
| Paired estimates and bootstrap intervals | `outputs/report_materials/results.json`, contrasts |
| Exact tests and adjusted p-values | `outputs/offline_analysis/tests.csv` |
| Three missing-output treatments | `outputs/offline_analysis/robustness.csv` |
| Category denominators and failures | `outputs/offline_analysis/categories.csv` |
| Review-subset and remaining-prompt accuracy | `outputs/offline_analysis/review.csv` |
| Completion counts and cross-tabulation | `outputs/report_materials/results.json`, content_by_condition and content_vs_strict; `runs/feedback_eval/official/review_pack/judgments.csv` |
| Initial and final status counts; 169 replacements | `runs/feedback_eval/official/execution_summary.json`; `analysis.json`; `overlay_manifest.json` |
| Five quoted case responses and exact source paths | `outputs/offline_analysis/cases.csv` |
| B3 item 159 failure | `outputs/report_materials/cases.json`; original record paths therein |
| Input-file hashes | `outputs/report_materials/source_manifest.json` |
| Full prompt components | `configs/feedback_eval/prompts.json`; `feedback_eval/feedback.py`; `feedback_eval/runner.py` |

Reproduce the supplementary analyses with `PYTHONDONTWRITEBYTECODE=1 NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python scripts/offline_report_analysis.py`. This command uses existing responses and makes no model calls.
