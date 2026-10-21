"""One bounded retry layer. Bind schema/tools to *each* model before fallback."""
import asyncio
import json
import os

import observability as O
from context_budget import prepare_messages

RETRIES = 2
MAX_OUTPUT_TOKENS = int(os.getenv("AGENT_OUTPUT_MAX_TOKENS", "1500"))


def make_models():
    if os.getenv("AGENT_MODEL_MODE", "auto") == "fake" or not os.getenv("OPENAI_API_KEY"):
        return None, None
    from langchain_openai import ChatOpenAI
    common = dict(temperature=0, timeout=30, max_retries=0, max_tokens=MAX_OUTPUT_TOKENS)
    primary = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), **common)
    fallback = ChatOpenAI(model=os.environ["FALLBACK_MODEL"], **common) if os.getenv("FALLBACK_MODEL") else None
    return primary, fallback


def retryable(error):
    return getattr(error, "status_code", None) in (429, 503) or type(error).__name__ in {"APITimeoutError", "APIConnectionError"}


def _usage(raw):
    usage = getattr(raw, "usage_metadata", None) or {}
    run = O.current()
    if run:
        run.usage_observed = run.usage_observed or bool(usage)
        run.input_tokens += usage.get("input_tokens", 0)
        run.output_tokens += usage.get("output_tokens", 0)


async def invoke(primary, fallback, messages, *, tools=None, schema=None, sleep=asyncio.sleep):
    from langchain_core.utils.function_calling import convert_to_openai_tool
    definitions = [convert_to_openai_tool(t) for t in tools or []]
    if schema:
        definitions.append(convert_to_openai_tool(schema))
    counter = None
    if hasattr(primary, "get_num_tokens_from_messages"):
        def counter(msgs, defs):
            try:
                # Tools/schema are part of the input budget, not just message text.
                result = primary.get_num_tokens_from_messages(msgs, allow_fetching_images=False)
                if not isinstance(result, int):
                    raise ValueError("Invalid offline token count")
                # Installed ChatOpenAI ignores tool schemas in its token counter.
                # UTF-8 bytes bound the schema contribution without a provider call.
                return result + len(json.dumps(defs, ensure_ascii=False).encode("utf-8")) + 32 * len(defs)
            except Exception:
                from context_budget import estimate_tokens
                return estimate_tokens(msgs, defs)
    view = prepare_messages(messages, definitions, counter=counter)
    for index, model in enumerate([primary, *([fallback] if fallback else [])]):
        if index:
            run = O.current()
            if run:
                run.fallbacks += 1
            O.note("model_fallback", model=getattr(model, "model_name", "test"))
        bound = model.with_structured_output(schema, method="function_calling", include_raw=True) if schema else model.bind_tools(tools) if tools else model
        for attempt in range(RETRIES + 1):
            run = O.current()
            if run:
                run.llm_calls += 1
            try:
                response = await bound.ainvoke(view)
                raw = response.get("raw") if schema else response
                _usage(raw)
                if schema:
                    if response.get("parsing_error") or response.get("parsed") is None:
                        raise ValueError("INVALID_STRUCTURED_OUTPUT")
                    return response["parsed"]
                return response
            except Exception as e:
                O.note("model_error", attempt=attempt + 1, statusCode=getattr(e, "status_code", None), errorCode=type(e).__name__)
                if retryable(e) and attempt < RETRIES:
                    if run:
                        run.retries += 1
                    O.note("model_retry", retry=attempt + 1)
                    await sleep(0.5 * 2 ** attempt)
                    continue
                if not retryable(e) or index or fallback is None:
                    raise
                break
