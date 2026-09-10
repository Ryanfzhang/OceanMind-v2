"""Simple delivery must stay bounded without relaxing scientific validity."""

from oceanx.runtime import (
    OCEAN_EXPERT_WORKSTREAM_POLICY,
    OCEAN_EXPLORATION_POLICY,
    OCEAN_RESEARCH_PARTNER_SYSTEM_PROMPT,
)
from oceanx.team.models import ChildAuthority
from oceanx.team.profiles import AGENT_PROFILES, profile_system_prompt


def _flatten(prompt: str) -> str:
    return " ".join(prompt.split())


def test_coordinator_finishes_requested_descriptive_outputs_without_scope_growth() -> None:
    prompt = _flatten(OCEAN_RESEARCH_PARTNER_SYSTEM_PROMPT)

    assert "simple map, ranking, or descriptive summary" in prompt
    assert "use one defensible method" in prompt
    assert "requested outputs as the stopping condition" in prompt
    assert "optional refinements are not a reason for another assignment" in prompt
    assert "Verify the calculations actually used" in prompt
    assert "do not waive the research-tree or evidence-review requirements" in prompt


def test_each_expert_stays_bounded_but_preserves_validation_and_error_repair() -> None:
    for profile in AGENT_PROFILES:
        if profile.authority is not ChildAuthority.EXPERT:
            continue
        prompt = _flatten(profile_system_prompt(profile))

        assert "choose one defensible method" in prompt
        assert "finish once the requested answer and outputs are supported" in prompt
        assert "Save each requested output after its essential checks pass" in prompt
        assert "not permission to skip validation, leave code errors unrepaired" in prompt
        assert "Successful execution alone does not validate a scientific result" in prompt
        assert "upgrading an unverified mechanism to a finding" in prompt


def test_early_saves_remain_checked_candidates_not_automatic_publication() -> None:
    prompt = _flatten(OCEAN_EXPERT_WORKSTREAM_POLICY)

    assert "Save each requested result as soon as its calculation and essential checks pass" in prompt
    assert "Do not postpone all saves until the end of a long program" in prompt
    assert "manufacture extra intermediate deliverables" in prompt
    assert "Early saving creates a candidate, not scientific acceptance" in prompt
    assert "correct or replace affected candidates if later evidence reveals an error" in prompt
    assert "The Coordinator alone reviews and publishes candidates" in prompt


def test_descriptive_scope_limits_leave_research_tree_terminal_requirements_intact() -> None:
    prompt = _flatten(OCEAN_EXPLORATION_POLICY)

    assert "Parents synthesize automatically ONLY when all children" in prompt
    assert "supported/contested remain nonterminal" in prompt
    assert "Only after all root hypotheses terminate and reflection is acknowledged" in prompt
    assert "Budget exhaustion/user interruption uses pause WITHOUT completion_summary" in prompt
