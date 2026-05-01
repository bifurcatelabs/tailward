# Synthesis recommendations — tested configurations

Living notes on which model + prompt + sampler combinations produce
useful session-synthesis output. The ``synthesis_worker`` writes a
JSON sidecar next to each snapshot (``~/.modmcp/projects/<ph>/snapshots/<ts>.meta.json``)
so runs are comparable: regenerate the same window with different
params, diff the markdown bodies, log what helped here.

This doc is the working surface; the README's "tested configurations"
section will absorb a curated subset once OSS-shippable.

## What we're optimizing for

- **Faithful capture.** Decisions, file paths, and concrete state
  preserved verbatim where possible. No paraphrase that loses specifics.
- **Compactness.** 200–400 words per snapshot keeps the merge step
  cheap when comprehensive synth eventually combines snapshots.
- **No hallucination.** Synthesis must not invent file paths, function
  names, or commitments that weren't in the input window.
- **Low recovery cost on bad output.** A snapshot that's vague or
  noisy is fine — bad ones get regenerated. A snapshot that's
  *confidently wrong* is worse than no snapshot.

## How to log a tested configuration

For each combo you try, capture in this file:

- Model identifier (e.g., ``qwen2.5-coder:32b-instruct-q5_K_M``)
- Sampler: temperature, top_p, top_k, min_p, repetition_penalty
- Whether ``enable_thinking`` was on
- Prompt variant (default ``SYSTEM_INCREMENTAL``, or note if customized)
- Window characteristics: rough turn count, dominant content shape
  (file edits / discussion / debugging)
- Verdict: faithful / compact / hallucination-free — yes/no/notes
- Wall-clock latency on your hardware (helpful for users picking models)

## Tested configurations

_None yet — fill in as you run experiments on the lab nodes._

### Template for new entries

```
### <model> @ T=<temp> top_p=<p> top_k=<k>

- **Window:** ~<N> turns, mostly <shape>
- **Latency:** <seconds> on <hardware label>
- **Faithful:** <yes/no/notes>
- **Compact:** <word count range>
- **Hallucination check:** <how you checked, what you found>
- **Verdict:** <recommend / avoid / conditional>
- **Sample snapshot:** ``snapshots/<timestamp>.md`` in <project>
```

## Open calibration questions

- **Optimal periodic threshold.** Default is 10k tokens since last
  snapshot. Calibrate against typical session shapes — discussion-heavy
  sessions probably want a smaller threshold; file-read-heavy sessions
  probably want larger.
- **Window cap.** Currently the prompt sees the last 16 assistant
  turns capped at ~8000 chars. May want this configurable per-model
  (smaller-context models need shorter windows).
- **Comprehensive synth strategy.** When the threshold-triggered
  comprehensive path lands, A/B between merge-snapshots and
  re-read-transcript. The merge approach is cheaper but its quality
  depends on the periodic snapshots being faithful (chicken-and-egg
  with the calibration above).

## Pointers

- Worker: ``src/modmcp/daemon/synthesis_worker.py``
- Comprehensive synth (existing): ``src/modmcp/phase1.py`` (``SYSTEM``)
- Incremental synth (new): ``synthesis_worker.SYSTEM_INCREMENTAL``
- Settings panel surfaces both prompts verbatim under "synth
  (comprehensive)" and "synth (incremental)".
- Sidecar metadata schema: see ``synthesis_worker._persist`` ``meta``
  dict.
