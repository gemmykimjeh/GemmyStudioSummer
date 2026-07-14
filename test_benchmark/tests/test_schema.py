"""Unit tests for the shared data schema."""

from __future__ import annotations

from harness.schema import Result, Step, Task, ToolResult, ToolSpec, Trajectory


def test_task_defaults():
    t = Task(id="t1", benchmark="b", prompt="do a thing")
    assert t.metadata == {}


def test_trajectory_add_and_tool_calls():
    traj = Trajectory()
    traj.add(Step(type="user_message", text="hi"))
    traj.add(Step(type="tool_call", name="foo", arguments={"a": 1}, output="ok"))
    traj.add(Step(type="assistant_message", text="done"))
    assert len(traj.steps) == 3
    calls = traj.tool_calls()
    assert len(calls) == 1
    assert calls[0].name == "foo"


def test_result_roundtrip_json():
    r = Result(task_id="t1", benchmark="b", agent="a", success=True, score=1.0,
               metrics={"x": 2})
    dumped = r.model_dump_json()
    back = Result.model_validate_json(dumped)
    assert back == r


def test_toolspec_default_parameters():
    spec = ToolSpec(name="noop", description="does nothing")
    assert spec.parameters == {"type": "object", "properties": {}}


def test_toolresult_error_flag():
    assert ToolResult(output="bad", is_error=True).is_error is True
    assert ToolResult(output="fine").is_error is False
