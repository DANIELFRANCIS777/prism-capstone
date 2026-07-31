from app.config import load_model_pricing


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost from the provider's reported usage and the price table - never from
    client-declared token counts."""
    price = load_model_pricing().get(model)
    if price is None:
        return 0.0
    input_cost = (prompt_tokens / 1_000_000) * price["input_per_1m"]
    output_cost = (completion_tokens / 1_000_000) * price["output_per_1m"]
    return round(input_cost + output_cost, 6)
