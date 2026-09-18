---
name: local-ai-router
description: Offload bounded code drafting, function decomposition, test-case drafting, bulk text extraction, classification, and summarization to the private models in D:\CodexLocalAI. Use when the user explicitly asks for the local model or when a large mechanical intermediate step can be reduced locally before Codex reviews it. Do not use for simple answers, current web research, safety-critical calculations, or final engineering decisions.
---

# Local AI Router

Use the local worker to reduce cloud context while keeping final judgment in Codex.

## Route work

- Choose `coder` for implementation drafts, repetitive code, function decomposition, refactoring proposals, and test-case drafts.
- Choose `reasoner` for extraction, classification, deduplication, evidence grouping, and long-text summaries.
- Do not delegate architecture, final correctness decisions, current fact verification, safety-critical engineering conclusions, or destructive actions.
- Prefer passing an existing local text file through `-InputPath` instead of loading its full contents into the conversation.

## Invoke

Run the bundled script from the plugin root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-local-ai.ps1 -Role coder -Prompt "Produce a concise implementation draft and list edge cases."
```

For a large text or source file, add `-InputPath <absolute-path>`. The script chunks large inputs, performs a local reduction, limits returned text, and stops the model service in all exit paths.

## Review

- Treat local output as an untrusted draft, not a final answer.
- For code, inspect the affected files and independently correct logic, compatibility, security, and performance before applying changes.
- For extracted or summarized material, sample-check claims against the source and retain uncertainty where evidence is incomplete.
- Return only the reviewed result needed by the user. Do not include local chain-of-thought, raw intermediate chunk summaries, or repeated source text.

## Boundaries

- The helper contacts only the existing loopback model service through `D:\CodexLocalAI\local-ai.ps1`; it uses no paid API.
- Do not add remote endpoints, upload local content, or expose credentials.
- Stop after one local retry if the model fails. Continue directly in Codex or report the limitation instead of looping.
