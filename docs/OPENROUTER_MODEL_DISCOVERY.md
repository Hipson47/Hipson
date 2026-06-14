# OpenRouter Model Discovery Design

Hipson can support OpenRouter model discovery, but it must not turn provider
catalog access into hidden AI execution. Discovery is metadata-only, opt-in, and
cacheable. Provider-backed review remains explicit sidecar work on bounded
packets.

## Current External Contract

OpenRouter exposes model metadata through `GET /api/v1/models` and keeps the
chat API OpenAI-compatible. Free usage is available through `openrouter/free`
and `:free` model variants, but free availability and limits are operational
constraints rather than release guarantees.

Useful official references:

- [OpenRouter Models API](https://openrouter.ai/docs/api/api-reference/models/get-models)
- [OpenRouter free router](https://openrouter.ai/openrouter/free)
- [OpenRouter free model limits](https://openrouter.ai/docs/api/reference/limits)
- [OpenRouter quickstart](https://openrouter.ai/docs/quickstart)

## Hipson Policy

1. No discovery call runs during `hipson work`, `hipson quality report`, or
   `hipson quality eval`.
2. Discovery must be explicit, for example:

   ```bash
   hipson provider models discover --provider openrouter --output runs/openrouter-models.json
   ```

3. Discovery output is metadata, not a recommendation to use every returned
   model.
4. Model profiles remain the curated product surface. Discovery only refreshes
   candidate facts such as model ID, context length, pricing, and supported
   modalities.
5. Free models are low-stakes lanes. They are not production reliability,
   security approval, release signoff, or sensitive-context lanes.

## Local Artifact Shape

```json
{
  "schema_version": "1.0",
  "provider": "openrouter",
  "created_at_utc": "...",
  "source": "https://openrouter.ai/api/v1/models",
  "models": [
    {
      "id": "openrouter/free",
      "name": "Free Models Router",
      "context_length": 0,
      "input_modalities": ["text"],
      "output_modalities": ["text"],
      "pricing": {"prompt": "0", "completion": "0"},
      "free": true
    }
  ],
  "policy": {
    "default_allow": false,
    "sensitive_allowed": false,
    "requires_profile_mapping": true
  }
}
```

## Allowlist Shape

```json
{
  "schema_version": "1.0",
  "provider": "openrouter",
  "allowed_models": {
    "openrouter/free": {
      "profiles": ["free_probe"],
      "sensitive_allowed": false,
      "max_context_chars": 60000,
      "notes": "Low-stakes first pass only."
    }
  }
}
```

## Doctor Extension

`hipson provider doctor` should eventually report:

- whether the OpenRouter API key is present without printing it;
- whether model discovery cache exists;
- cache age;
- whether configured profile models still appear in the cache;
- whether any profile uses a model outside the local allowlist;
- whether free-model use is enabled only for low-risk profiles.

## Failure Posture

If discovery is unavailable, Hipson should continue to work locally. It should
not fail `hipson work`, `hipson verify`, `hipson quality report`, or evidence
commands. The correct fallback is:

- keep existing curated profiles;
- mark discovery cache as missing or stale;
- recommend explicit `provider models discover` only when the user wants live
  provider metadata.
