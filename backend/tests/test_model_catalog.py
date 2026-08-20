from app.model_catalog import list_models_for_allowlist


def test_only_allowlisted_names_are_returned():
    models = list_models_for_allowlist(["fast"])
    assert [m["id"] for m in models] == ["fast"]


def test_alias_is_owned_by_prism_not_a_single_provider():
    """An alias can fail over between providers (fast: alpha -> beta) or have
    no single primary at all (auto), so attributing it to one provider would
    be wrong the moment the config's fallbacks change."""
    (fast,) = list_models_for_allowlist(["fast"])
    assert fast["owned_by"] == "prism"


def test_literal_model_is_owned_by_its_real_provider():
    (model,) = list_models_for_allowlist(["openai/gpt-oss-120b"])
    assert model["owned_by"] == "groq"

    (model,) = list_models_for_allowlist(["gemini-2.5-flash-lite"])
    assert model["owned_by"] == "gemini"


def test_response_matches_the_openai_model_object_shape():
    (model,) = list_models_for_allowlist(["fast"])
    assert set(model) == {"id", "object", "created", "owned_by"}
    assert model["object"] == "model"
    assert isinstance(model["created"], int)


def test_results_are_sorted_by_id_for_a_stable_response():
    ids = [m["id"] for m in list_models_for_allowlist(["smart", "auto", "fast"])]
    assert ids == sorted(ids)


def test_names_no_longer_in_config_are_dropped_not_raised():
    """An operator retiring a model shouldn't break discovery for every key
    whose allowlist still references it - and advertising something that
    would 404 on use is worse than omitting it."""
    models = list_models_for_allowlist(["fast", "retired-model-that-no-longer-exists"])
    assert [m["id"] for m in models] == ["fast"]


def test_empty_allowlist_returns_nothing():
    assert list_models_for_allowlist([]) == []


def test_newly_registered_models_are_listed():
    ids = [m["id"] for m in list_models_for_allowlist(["openai/gpt-oss-20b", "gemini-2.5-flash"])]
    assert ids == ["gemini-2.5-flash", "openai/gpt-oss-20b"]
