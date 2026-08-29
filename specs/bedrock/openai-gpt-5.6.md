# Bedrock `invoke_model` contract — OpenAI GPT-5.6 family

Measured 2026-08-28 against account `741448939267`, region `us-east-1`, by direct
`bedrock-runtime.invoke_model` probes. Every claim below is something a probe returned,
not something a document asserts.

**Do not re-probe to confirm these.** Each probe is a billable inference call. Build against this
file and test with fakes.

## Models and inference profiles

All three are `INFERENCE_PROFILE`-only, so a bare `modelId` of `openai.gpt-5.6-*` will not
invoke. Use the profile id.

| Model | Profile id used here | Input modalities | Streaming |
| --- | --- | --- | --- |
| `openai.gpt-5.6-luna` | `global.openai.gpt-5.6-luna` | `TEXT`, `IMAGE` | yes |
| `openai.gpt-5.6-terra` | `global.openai.gpt-5.6-terra` | `TEXT`, `IMAGE` | yes |
| `openai.gpt-5.6-sol` | `global.openai.gpt-5.6-sol` | `TEXT`, `IMAGE` | yes |

`us.openai.gpt-5.6-*` profiles also exist and are ACTIVE; `global.*` is what this project uses.
`startOfLifeTime` for the family is 2026-08-13.

## The AWS documentation is stale for this family

<https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-openai.html> states:

> The OpenAI models support only text input and text output.

That is false for GPT-5.6. `get-foundation-model` reports `inputModalities: ['TEXT', 'IMAGE']`
for all three, and a probe with an inline PNG transcribed it correctly. The doc page was written
for the 2025 `gpt-oss` family. **Trust `get-foundation-model` over the prose page.**

## Request body: OpenAI Chat Completions, NOT the Anthropic envelope

The Anthropic envelope this project used for Claude is rejected outright:

```text
body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 64, "messages": [...]}
-> ValidationException: {"error":{"code":"unknown_parameter",
     "message":"Unknown parameter: 'anthropic_version'.","param":"anthropic_version",
     "type":"invalid_request..."}}
```

The working shape, verified:

```python
body = {
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Transcribe the text in this image. Output only the text.",
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }
    ],
    "max_completion_tokens": 2000,
    # optional:
    "reasoning_effort": "medium",
}
response = client.invoke_model(modelId="global.openai.gpt-5.6-luna", body=json.dumps(body))
```

Field notes:

- **`max_completion_tokens`**, not `max_tokens`.
- Images travel as an `image_url` part whose `url` is a `data:image/png;base64,...` URI — the
  OpenAI vision convention, not Bedrock's `{"type": "image", "source": {...}}` block.
- `stream` must be `false` (or omitted) for `invoke_model`.
- `model` inside the body may be omitted; Bedrock fills it from the header.
- A `developer`-role message is the system-prompt equivalent in this shape.

## `reasoning_effort` has a closed value set

| Value | Result |
| --- | --- |
| `none` | accepted |
| `low` | accepted |
| `medium` | accepted |
| `high` | accepted |
| `minimal` | **rejected** — `{"code":"unsupported_value","message":"Unsupported value: 'reasoning_effort'..."}` |

So the valid set is `{none, low, medium, high}`. `minimal` exists in OpenAI's own API and is not
available here — do not pass it through from user input without validating against this set.

## Response body

```json
{
  "choices": [{"finish_reason": "stop", "index": 0, "message": {"content": "..."}}],
  "created": ..., "id": ..., "model": ..., "object": ...,
  "service_tier": ..., "system_fingerprint": ...,
  "usage": {
    "completion_tokens": 11,
    "completion_tokens_details": {
      "accepted_prediction_tokens": 0, "audio_tokens": 0,
      "reasoning_tokens": 0, "rejected_prediction_tokens": 0
    },
    "prompt_tokens": 177,
    "prompt_tokens_details": {"audio_tokens": 0, "cache_write_tokens": 0, "cached_tokens": 0},
    "total_tokens": 201
  }
}
```

`system_fingerprint` and `model` are the two fields worth folding into a cache key — they are the
only signal that the served model changed underneath a stable profile id.

## The silent-failure trap — this is the important part

These models spend output budget on internal reasoning **before** producing content, and the
reasoning spend is not visible until the response comes back.

Measured, same prompt and image, only the budget differing:

| `max_completion_tokens` | `finish_reason` | `reasoning_tokens` | `completion_tokens` | `message.content` |
| --- | --- | --- | --- | --- |
| 24 | `length` | 24 | 24 | **`None`** |
| 2000 | `stop` | 0 | 11 | `'rmspec probe 7431'` |

At a tight budget the entire allowance went to reasoning and **`content` came back `None` with no
exception raised**. A client that does `response["choices"][0]["message"]["content"].strip()` gets
an `AttributeError` at best; one that treats a falsy content as "no text found" caches an empty
transcription for a page that has text, and the cache key gives no hint that anything went wrong.

**Required adapter behaviour:**

1. Treat `content is None` as an error, never as an empty transcription.
2. Treat `finish_reason == "length"` as an error distinct from a refusal or an empty page — it means
   the budget was too small, which is a caller bug, not a property of the image.
3. Surface `reasoning_tokens` so a caller can see budget being consumed by reasoning.
4. Never write a cache row for a response that did not reach `finish_reason == "stop"`.

## Reasoning-depth comparison, for tier selection

Identical prompt (an over-correction trap: list only certain misspellings in
`'meet Dr Chen re: Q3 buget at 3pm Thu'`), 600-token cap. All three answered correctly (`buget`).

| Model | `reasoning_tokens` | `completion_tokens` |
| --- | --- | --- |
| luna | 48 | 56 |
| sol | 63 | 71 |
| terra | 252 | 260 |

No metadata field distinguishes the three — `get-foundation-model` returns nothing beyond the
common fields. Reasoning spend on identical work is the only empirical ranking available, and it
puts terra substantially deeper than the other two.

This project therefore uses **luna for the per-page read** and **terra for the merge and
adjudication step**. That is one data point, not a benchmark, so both are settings fields rather
than constants.

## Cost discipline

Every call in this file was billable. The project's test suite must never construct a
`bedrock-runtime` client; `LlmPort` exists so tests use fakes. If a future contract question needs
a probe, add it here rather than re-running it.
