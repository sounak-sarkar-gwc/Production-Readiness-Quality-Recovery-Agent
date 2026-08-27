"""Core agent: a single, stateless tool-calling loop, run fresh per request.

Unlike a persistent multi-turn session, nothing here is remembered between
HTTP calls -- each phase endpoint builds its own message list, runs the
Gemini tool-use loop until the model stops asking for tools, and returns the
final narrative plus the raw specialist results it collected along the way.

Uses the Gemini API (free tier via Google AI Studio) rather than a paid
provider -- xAI's Grok API has no comparable free tier as of this writing.

Guardrails in this module:
  - a request timeout on every Gemini call (agent_timeout_seconds)
  - retry with backoff on transient upstream errors only (5xx, 429) --
    non-transient errors (bad API key, unknown model, malformed request)
    fail immediately instead of burning three retries on something that
    will never succeed
  - every Gemini/tool-execution failure is translated into a typed
    UpstreamServiceError/ConfigurationError rather than an unhandled
    exception, so server.py's exception handlers can turn it into a clean
    JSON response instead of a raw traceback
  - a tool implementation that raises is caught and fed back to the model
    as a tool error rather than crashing the whole request -- a malformed
    or hallucinated tool argument shouldn't take the endpoint down
"""

import logging

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import settings
from .errors import ConfigurationError, UpstreamServiceError

logger = logging.getLogger("prq.agent_core")

MODEL = settings.agent_model
MAX_TOOL_ITERATIONS = 6

_client = None


def get_client():
    global _client
    if _client is None:
        if not settings.agent_configured:
            raise ConfigurationError(
                "GEMINI_API_KEY is not set. Add it to backend/.env before calling agent endpoints "
                "(get a free-tier key at https://aistudio.google.com/apikey)."
            )
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(settings.agent_timeout_seconds * 1000)),
        )
    return _client


def _is_transient(exc: BaseException) -> bool:
    """5xx is always worth retrying; 429 (rate limit) is too, since a short
    backoff often clears it. Other 4xx (bad request, unknown model, auth
    failure) will never succeed on retry -- fail fast instead."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return exc.code == 429
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.agent_max_retries),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_transient),
)
def _generate(client, **kwargs):
    return client.models.generate_content(**kwargs)


def _to_gemini_tools(tools: list) -> list:
    """Converts our Anthropic-shaped tool schema ({name, description,
    input_schema}) into Gemini FunctionDeclarations, so tools.py/server.py
    don't need to know which provider is behind run_agent_turn()."""
    declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _execute_tool(name: str, args: dict, tool_impls: dict) -> dict:
    impl = tool_impls.get(name)
    if impl is None:
        logger.warning("Model requested unknown tool %r", name)
        return {"error": f"Unknown tool {name}"}
    try:
        return impl(**args)
    except TypeError as exc:
        # Model supplied the wrong argument shape for this tool.
        logger.warning("Tool %r called with bad arguments %r: %s", name, args, exc)
        return {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # a specialist bug shouldn't take the request down
        logger.exception("Tool %r raised while handling args %r", name, args)
        return {"error": f"{name} failed: {exc}"}


def run_agent_turn(system_prompt: str, user_message: str, tools: list, tool_impls: dict) -> dict:
    """Runs one stateless tool-calling turn and returns
    {"final_text": str, "tool_calls": [{"name", "input", "result"}]}.

    Raises ConfigurationError if no API key is set, or UpstreamServiceError
    if Gemini fails/times out after retries."""
    client = get_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=_to_gemini_tools(tools),
        max_output_tokens=settings.agent_max_output_tokens,
    )
    contents = [types.Content(role="user", parts=[types.Part(text=user_message)])]
    collected_calls = []

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            response = _generate(client, model=MODEL, contents=contents, config=config)
        except genai_errors.APIError as exc:
            logger.error("Gemini call failed (code=%s): %s", getattr(exc, "code", "?"), exc)
            raise UpstreamServiceError(
                f"The AI service failed to respond (upstream code {getattr(exc, 'code', '?')})."
            ) from exc
        except TimeoutError as exc:
            logger.error("Gemini call timed out after %.0fs", settings.agent_timeout_seconds)
            raise UpstreamServiceError("The AI service timed out.") from exc

        if not response.candidates:
            raise UpstreamServiceError("The AI service returned an empty response.")

        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if p.function_call]

        if not function_calls:
            final_text = "".join(p.text for p in parts if p.text)
            return {"final_text": final_text, "tool_calls": collected_calls}

        contents.append(candidate.content)
        response_parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            result = _execute_tool(fc.name, args, tool_impls)
            collected_calls.append({"name": fc.name, "input": args, "result": result})
            response_parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=response_parts))

    logger.warning("Reached max tool iterations (%d) without a final answer", MAX_TOOL_ITERATIONS)
    return {
        "final_text": "Reached max tool iterations without a final answer.",
        "tool_calls": collected_calls,
    }
