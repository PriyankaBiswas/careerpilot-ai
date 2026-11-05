"""Opt-in OpenAI API mode (AI_PROVIDER='openai'). Not used in the default manual mode."""
from .prompts import SYSTEM_RULES


class AIConfigError(Exception):
    pass


def generate(prompt, api_key, model, client_factory=None):
    """Send an already-built CareerPilot prompt to the OpenAI Responses API and return the text."""
    if not api_key:
        raise AIConfigError("AI_PROVIDER is 'openai' but OPENAI_API_KEY is not configured.")
    if client_factory is None:
        from openai import OpenAI  # imported lazily so manual mode never needs the package configured
        client_factory = OpenAI
    client = client_factory(api_key=api_key, timeout=90)
    response = client.responses.create(
        model=model,
        instructions=SYSTEM_RULES,
        input=prompt,
        max_output_tokens=4000,
    )
    return response.output_text
