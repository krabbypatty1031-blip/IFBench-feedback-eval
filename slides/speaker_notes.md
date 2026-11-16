# Speaker Notes — COMP4136 Mini-Project (IFBench feedback revision)

讲者先用占位符：[Member A] 第 1–3 页，[Member B] 第 4–5 页，[Member C] 第 6–8 页，[Member D] 第 9–10 页。名单和分工确定后替换即可。
每页约 1 分钟（英文约 110–130 词），全程约 9.5 分钟，留出切换讲者的时间。

---

## Slide 1 — Title（[Member A]，约 30 秒）

Good morning. We are Group [XX]. Our project asks a simple question: when a large language model fails an explicit output constraint, does telling it the verifier's verdict help it fix the answer? We evaluate this on IFBench with two models on the HKBU gateway. I will introduce the background and research questions, [Member B] will explain the experimental design, [Member C] will present the results and mechanism, and [Member D] will walk through real cases and conclude.

> 中文要点：自我介绍；一句话说明问题；交代四人分工顺序。

## Slide 2 — LLMs still miss verifiable output constraints（[Member A]，约 60 秒）

Instruction following means obeying concrete requirements such as word counts, formats, or banned words. IFEval made this measurable with 25 types of constraints that a program can check. IFBench extends this with 58 new, out-of-domain constraints, and shows that even strong models fail many of them. A natural fix is revision: Self-Refine lets a model critique and rewrite its own output. However, Huang et al. showed that self-correction without any external signal is unreliable. So the open question for us is whether an external, automatic verdict makes revision work where self-checking does not.

> 中文要点：指令遵循=可程序检查的约束；IFEval→IFBench；Self-Refine 是自我修正；Huang 等人指出没有外部信号的自我修正不可靠，引出我们的问题。

## Slide 3 — Research questions（[Member A]，约 60 秒）

We turn this into four research questions. RQ1: does a single revision round improve strict compliance over the first answer, B0? RQ2: does a verifier verdict beat simply asking the model to check itself, that is B2 against B1? RQ3: if we tell the model exactly which constraint failed, does that add anything beyond the overall verdict, B3 against B2? And RQ4 looks inside the numbers: which answers get repaired, and which correct answers get broken by revision. Now [Member B] will explain how we set up the experiment.

> 中文要点：四个问题逐一对应后面的结果页；最后交接给 [Member B]。

## Slide 4 — Experimental design（[Member B]，约 60 秒）

We use the official IFBench test file. After holding out a development set, 260 prompts remain for testing. We query two models on the HKBU GenAI gateway, named gpt-4.1 and gpt-4o, at temperature zero, with exactly one revision round. The table on the right shows the four conditions. B0 is the first answer. B1 asks the model to re-check the constraints itself. B2 adds the verifier's overall PASS or FAIL. B3 lists PASS or FAIL for each constraint together with the original requirement text. Our main metric is the official strict prompt-level accuracy: a prompt counts only if every constraint is satisfied.

> 中文要点：数据、模型、解码设置、四种条件、主指标 strict prompt-level（全部约束通过才算通过）。

## Slide 5 — Method flowchart（[Member B]，约 60 秒）

This is the pipeline. Each prompt first produces one B0 answer. B1 branches directly from that answer with a self-check request, without seeing the verifier. For B2 and B3, the official strict verifier scores B0 first, and its result is turned into feedback: an overall verdict for B2, per-constraint verdicts for B3. Importantly, all three revisions start from the same B0 answer and run once, independently, so differences between them come from the revision signal, not from different starting points. Finally, every revised answer is re-scored with the same verifier. Comparisons are paired on the same prompts. Now [Member C] will present the results.

> 中文要点：三个分支都从同一个 B0 出发、各改一次、互相独立；用同一个验证器重新评分；配对比较。交接给 [Member C]。

## Slide 6 — Main results table（[Member C]，约 60 秒）

Here are the prompt-level accuracies. Rows are conditions, columns are the two models, with strict and loose scoring. The bold row is B2: it is the best condition in all four columns. B1 and B3 also improve on the first answer, but by less. The ranking is the same for both models and for both scoring modes, so the pattern is not an artifact of one model or of strict string matching. The next slide shows the improvement directly as a paired comparison against B0.

> 中文要点：B2 四列全部最好；两个模型、strict/loose 排序一致。表中数字不需要逐个念。

## Slide 7 — Paired gain over B0（[Member C]，约 60 秒）

This chart shows the paired gain over B0, in percentage points, on the same prompts. Green, B2, gives the largest gain, close to ten points on both models. Pink, B3, is also clearly positive. Orange, B1, the self-check condition, gives only a small gain. Statistically, B2 and B3 are significant on both models, while B1 is not. We also checked different ways of handling the few missing responses, and the B2 conclusion does not change. Detailed intervals and tests are in the report appendix. So, answering RQ1 and RQ2: a verifier signal helps; self-checking alone does not reliably help.

> 中文要点：B2 提升最大，B3 也显著，B1 不显著；对缺失值处理方式稳健；细节在附录。回答 RQ1、RQ2。

## Slide 8 — Why B2 beats B1（[Member C]，约 60 秒）

Why does B2 beat B1? The main reason is not that B1 repairs fewer failures; it breaks more answers that were already correct. Among answers that passed at B0, B1 turned 13.8 percent into failures on gpt-4.1 and 10.8 percent on gpt-4o; for B2 it is about half that. On gpt-4.1, B1 rewrote 123 of 138 passing answers, while B2 left 103 of them unchanged; gpt-4o shows the same pattern. Note how B2 works: it gives the verdict and, for passing answers, says the answer may stay unchanged, so the gain reflects both parts. Finally, B3 and B2 are not significantly different, and our sample is too small to separate them, so we prefer the simpler B2. [Member D] will now show real examples.

> 中文要点：B1 输在"改坏已通过的答案"；B2 的提升来自"判定 + 允许保留"两部分；B3 与 B2 无显著差异、样本量不足以区分，所以选更简单的 B2（不说 B3 更差）。交接给 [Member D]。

## Slide 9 — Case study（[Member D]，约 60 秒）

Two real cases, copied directly from our run records, one per model. At the top, gpt-4o, item 296: the answer must contain no whitespace at all. B0 wrote one long string with no spaces and passed. Asked to self-check, B1 rewrote it and slipped three spaces back in, "Inclass theories", so it failed. B2 was told the answer had passed; it still passed. This is the over-rewriting effect from the previous slide in its smallest form. At the bottom, gpt-4.1, item 173: at most 20 percent of the words may be stop words. B0 gave a correct counterexample in normal prose and failed the ratio; B1 also failed. After the FAIL verdict, B2 compressed the wording into short phrases such as "Pointwise limit:" and passed, keeping the same counterexample.

> 中文要点：上面 gpt-4o 第 296 题：B0 通过，B1 自查时反而加回空格而失败，B2 仍通过；下面 gpt-4.1 第 173 题：B0、B1 都失败，B2 收到 FAIL 后压缩虚词改对，数学内容不变。例子均原样截取。

## Slide 10 — Limitations and conclusion（[Member D]，约 60 秒）

To conclude: a simple pass/fail verdict, combined with permission to keep a passing answer, gave the most reliable improvement, and self-checking alone did not. Our scope is limited: two models on the HKBU gateway, one revision round, and a single run at temperature zero. Content review covered all 2080 test identities; 2046 were complete and 8 could not be judged after a campus content filter. It was a single rater, and content quality was already near the ceiling, so we can only say we saw no clear content damage. Protocol deviations, such as the post-hoc merging rule for reruns, are documented in the report. Contributions are listed at the bottom. Thank you for listening.

> 中文要点：结论一句话；三条局限；协议偏离见报告；指一下分工行即可。
