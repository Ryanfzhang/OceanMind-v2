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
    assert "Simple questions need no research tree" in prompt
    assert "additional review only when it would resolve a material question" in prompt


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


def test_question_sufficiency_does_not_force_hypothesis_terminal_states() -> None:
    prompt = _flatten(OCEAN_EXPLORATION_POLICY)

    assert "supported and contested remain nonterminal hypothesis states" in prompt
    assert "neither all-root termination nor an established hypothesis is required" in prompt
    assert "A supported association or a sufficiently grounded negative answer can be answered" in prompt
    assert "pause(decision=answered, completion_summary=...)" in prompt
    assert "pause(decision=unable_to_answer, completion_summary=...)" in prompt
    assert "Budget exhaustion/user interruption uses pause WITHOUT completion_summary" in prompt


def test_expert_can_change_tests_without_expanding_hypothesis_authority() -> None:
    prompt = _flatten(OCEAN_EXPERT_WORKSTREAM_POLICY)

    assert "Choose and replace methods autonomously" in prompt
    assert "within the authorized question and budget" in prompt
    assert "suggested_path and hints are optional, replaceable guidance" in prompt
    assert "Empty leads, limitations, and path_deviations are valid" in prompt
    assert "no separate preregistration call is required" in prompt
    assert "You cannot create or modify hypotheses, write effects, or change their states" in prompt
    assert "Use another code call only" not in prompt


def test_coordinator_reasons_after_results_without_mandatory_review_forms() -> None:
    prompt = _flatten(OCEAN_RESEARCH_PARTNER_SYSTEM_PROMPT)

    assert "Whenever an Expert result arrives, consider what it adds or changes" in prompt
    assert "whether further analysis would add knowledge worth its cost" in prompt
    assert "Judge the evidence before updating a state" in prompt
    assert "not five mandatory paragraphs or a separate review submission" in prompt
    assert "Evidence direction, inference level, and answer sufficiency are distinct" in prompt
