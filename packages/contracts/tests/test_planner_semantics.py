"""Semantic validator: ≥60% of plan tasks must reference the spike thesis (01-TECH_SPEC §5 A3).

Not schema-expressible — the schema guarantees shape, this guarantees the plan
is actually about the student's spike.
"""

import pydantic
import pytest

from spykt_contracts import PlannerResult, SpikeAlignmentError, validate_planner_result


def make_plan(task_texts: list[tuple[str, str, str]]) -> PlannerResult:
    """Build a plan from (title, spike_alignment, rationale) triples."""
    return PlannerResult.model_validate(
        {
            "week_plan": {
                "tasks": [
                    {
                        "title": title,
                        "spike_alignment": alignment,
                        "effort_hrs": 2.0,
                        "evidence_required": "artifact",
                        "rationale": rationale,
                    }
                    for title, alignment, rationale in task_texts
                ]
            },
            "roadmap_diff": [],
            "autonomy_level_required": "L1",
            "agenda_seed_for_coach_session": "",
        }
    )


ON_SPIKE = ("Extend robotics controller", "robotics spike milestone", "advances the robotics thesis")
OFF_SPIKE = ("Study for chemistry quiz", "general academics", "keeps GPA stable")


def test_exactly_60_percent_passes():
    plan = make_plan([ON_SPIKE, ON_SPIKE, ON_SPIKE, OFF_SPIKE, OFF_SPIKE])
    assert validate_planner_result(plan, ["robotics"]) == pytest.approx(0.6)


def test_below_threshold_raises():
    plan = make_plan([ON_SPIKE, ON_SPIKE, OFF_SPIKE, OFF_SPIKE, OFF_SPIKE])
    with pytest.raises(SpikeAlignmentError, match="2/5"):
        validate_planner_result(plan, ["robotics"])


def test_all_aligned_passes():
    plan = make_plan([ON_SPIKE, ON_SPIKE, ON_SPIKE])
    assert validate_planner_result(plan, ["robotics"]) == 1.0


def test_accepts_raw_dict_input():
    plan_dict = make_plan([ON_SPIKE, ON_SPIKE]).to_contract_dict()
    assert validate_planner_result(plan_dict, ["robotics"]) == 1.0


def test_dict_input_is_structurally_validated_first():
    """Dict input goes through PlannerResult.model_validate (the pydantic mirror of
    the a3_planner schema) before any semantic check, so a malformed plan fails
    structurally — here with the >5-tasks violation."""
    plan_dict = make_plan([ON_SPIKE, ON_SPIKE]).to_contract_dict()
    plan_dict["week_plan"]["tasks"] = plan_dict["week_plan"]["tasks"] * 3  # 6 tasks
    with pytest.raises(pydantic.ValidationError, match="tasks"):
        validate_planner_result(plan_dict, ["robotics"])


def test_zero_task_plan_is_vacuously_aligned():
    """Protected weeks (01-TECH_SPEC §6) can ship a zero-task plan; the ≥60% rule
    is vacuously satisfied — never a ZeroDivisionError."""
    plan = make_plan([])
    assert validate_planner_result(plan, ["robotics"]) == 1.0


def test_matching_is_case_insensitive():
    plan = make_plan([("Robotics sprint", "", "prep"), ("ROBOTICS demo", "", "show")])
    assert validate_planner_result(plan, ["robotics"]) == 1.0


def test_multi_term_thesis_any_term_counts():
    plan = make_plan(
        [
            ("Write ML pipeline", "", "infra"),
            ("Robotics field test", "", "data collection"),
            OFF_SPIKE,
        ]
    )
    assert validate_planner_result(plan, ["robotics", "ML pipeline"]) == pytest.approx(2 / 3)


def test_word_boundary_prevents_substring_false_positives():
    plan = make_plan([("Start the semester strong", "", "get organized")])
    with pytest.raises(SpikeAlignmentError):
        validate_planner_result(plan, ["art"])


def test_empty_terms_is_an_error_not_a_pass():
    plan = make_plan([ON_SPIKE])
    with pytest.raises(ValueError, match="spike_thesis_terms"):
        validate_planner_result(plan, [])
    with pytest.raises(ValueError, match="spike_thesis_terms"):
        validate_planner_result(plan, ["  ", ""])
