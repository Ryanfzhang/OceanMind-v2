"""Process-local model policy for benchmarks; never writes Desktop settings."""
from __future__ import annotations

BENCHMARK_MODEL = "deepseek-v4-pro"


def install_oceanx_models():
    from oceanx import model_config

    original = model_config._profile_payload

    def benchmark_profile(settings, *, role="coordinator"):
        # Use the Coordinator's configured endpoint/credential for every role.
        # Override at profile resolution so existing imported loader aliases and
        # auxiliary model calls get exactly the same policy.
        name, raw = original(settings, role="coordinator")
        return name, {**raw, "last_model": BENCHMARK_MODEL,
                      "default_model": BENCHMARK_MODEL,
                      "skill_reviewer_model": BENCHMARK_MODEL}

    model_config._profile_payload = benchmark_profile
    return {"model": BENCHMARK_MODEL, "roles": ["coordinator", "expert", "skill_curator"],
            "api_profile": "coordinator", "scope": "benchmark_process_only"}
