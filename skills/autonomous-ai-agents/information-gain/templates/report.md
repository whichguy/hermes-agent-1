# Key Questions to Improve the Response

**Prompt:** {{problem}}
{{evidence}}
- **Goal:** {{goal}}
- **Response type:** {{decision}}
- **Success criteria:** {{success_criteria}}

**Baseline response** (the best answer to the prompt right now — what we measure value against):

{{baseline_plan}}

## ⭐ Key questions, ranked by weight — answer these to improve the response

Each **weight** = *exploration value* = how much answering the question is expected to improve your
response to the prompt = **answerability × √(uncertainty × value-of-answering)**. Higher = answering
it most improves the response, so answer it sooner.

{{ranked_list}}

{{discarded_note}}

<details><summary>Detailed scores per question</summary>

`uncert` = unknown & reducible · `answer-value` (EVSI) = Σ P(answer)·response-change·stakes ·
`answerable` = resolvable if explored · `assume-if-skipped` = the most-likely answer to proceed on.

{{table}}

</details>

---
{{meta}}
