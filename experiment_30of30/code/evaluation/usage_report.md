# Token usage and cost report

## Final full-dataset run (produces `output.csv` / `submission/output_candidate.csv`)

The final run is fully deterministic Python (standard library only). It makes no calls to
any language model.

| Provider | Model | Model calls | Input tokens | Output tokens | Total tokens |
|---|---|---|---|---|---|
| — | — (deterministic solver) | 0 | 0 | 0 | 0 |
| **Overall** | | **0** | **0** | **0** | **0** |

| Metric | Value |
|---|---|
| Requests processed | 518 (plus 32 sample requests solved jointly for shared activity windows) |
| Total tokens | 0 |
| Average tokens per request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |
| Wall-clock runtime | about 3 s on one CPU core |

## Development-time model use (not part of the final run)

* Facts in the 11 screenshots and 4 PDFs were read once during development by the coding
  assistant (Claude Code, a multimodal model). PDF text was also extracted with
  `pdftotext`. The facts were recorded with notes in `code/evidence_transcriptions.json`,
  which the solver loads like any other evidence parser output.
* Rule discovery, code writing and evaluation were done interactively in the same coding
  session. That session's token usage is recorded by the harness, not by this solution, so
  it is not reported as part of the final run.
* No API keys, credentials or model endpoints are used or needed to reproduce `output.csv`.
