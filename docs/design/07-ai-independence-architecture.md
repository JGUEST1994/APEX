# 07. AI independence architecture

## Principle

APEX must keep running, trading (paper for now) and researching with every external AI provider unavailable. External AI only ever **adds** explanations, summaries and suggestions.

## Layers

| Layer | Examples | Failure mode if unavailable |
|---|---|---|
| Deterministic core (A) | indicators, calendars, validation, backtests, risk, execution | n/a: always local |
| Local intelligence (B) | LightGBM regime classifier, isolation-forest anomaly detection, volatility forecasts (GARCH / HAR), strategy ranking models | Strategy falls back to its non-ML rule or stays flat; never blocks risk or execution |
| External AI (C) | Claude, OpenAI, local LLM via Ollama or llama.cpp | Feature hidden in UI; research continues with keyword and local-embedding search |

## Enforcement

- `apex.ai.provider` exposes `LanguageModel` (`available()`, `complete()`) and a registry. The default `NullProvider` is always unavailable, so code paths that use AI are written to handle `AIUnavailable` from day one (tested).
- Core modules do not import `apex.ai` (import-linter contract planned for Phase 2 to make this a failing check).
- AI output never reaches the execution layer. An AI-suggested strategy is source code or a spec that a human reviews and that then enters the normal lifecycle at `experimental`.
- Embeddings for search come from a local sentence-transformer model (384-dim, e.g. all-MiniLM-L6-v2) so the research library does not depend on a remote embedding API.

## Local ML lifecycle (Component B, Phase 7)

1. Train offline in the lab worker from versioned data; record data hashes, features, hyper-parameters and seed.
2. Validate with purged walk-forward; compare against a no-ML baseline. Keep the model only if it improves validated out-of-sample results.
3. Export to ONNX where supported (scikit-learn via skl2onnx, LightGBM/XGBoost via onnxmltools; PyTorch native) and register it with a version and checksum.
4. A model becomes active in paper trading only by explicit approval; rollback is selecting the previous version.
5. Inference in the trading process uses ONNX Runtime only, so training libraries are not needed in production.

## Adapters for Component C (Phase 2+)

Each adapter is a small class registered with `register_provider(name, factory)` and configured by `APEX_AI_PROVIDER` and `APEX_AI_API_KEY`. Prompts and responses used for research are logged to the research journal with provider and model name, so findings stay attributable and reproducible as far as possible.
