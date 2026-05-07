from __future__ import annotations

import argparse
import ast
import importlib.util
import inspect
import json
import re
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from agentdojo.functions_runtime import FunctionCall, TaskEnvironment
from agentdojo.yaml_loader import ImportLoader


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
DEFAULT_SUMMARY_ROOT = PROJECT_ROOT / "src" / "banking_v6_auto" / "generate_tools_v17_gpt-5.4"
DEFAULT_USER_TASK_LOG_ROOT = PROJECT_ROOT / "src" / "banking_v6_auto" / "UserTask_log"
DEFAULT_LOG_DIR = CURRENT_DIR / "log_security_validator"
SUITES_DIR = PROJECT_ROOT / "src" / "agentdojo" / "data" / "suites"
BANKING_DEPENDENCY_PARAM_NAMES = {
    "account",
    "banking",
    "bank_account",
    "filesystem",
    "user_account",
}



def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute banking injection scenarios in winning-summary order and evaluate utility + security."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help=(
            "Scenario module name or path, for example "
            "'user_task_5' or 'src/banking_code/user_task_5.py'. "
            "Injection-style names are also accepted."
        ),
    )
    parser.add_argument(
        "--summary-path",
        help="Optional path to a concrete summary JSON. If omitted, the validator resolves the winning summary automatically.",
    )
    parser.add_argument(
        "--summary-root",
        default=str(DEFAULT_SUMMARY_ROOT),
        help=f"Root directory containing UserTaskX summaries. Default: {DEFAULT_SUMMARY_ROOT}",
    )
    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help=f"Directory to write validator logs. Default: {DEFAULT_LOG_DIR}",
    )
    return parser.parse_args()


def _resolve_scenario_path(raw_scenario: str) -> Path:
    scenario_path = Path(raw_scenario)
    if scenario_path.exists():
        return scenario_path.resolve()

    candidate = CURRENT_DIR / f"{raw_scenario}.py"
    if candidate.exists():
        return candidate.resolve()

    raise FileNotFoundError(f"Cannot find scenario module for '{raw_scenario}'.")


def _load_module(module_path: Path, module_name: str | None = None) -> ModuleType:
    module_name = module_name or f"dynamic_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for '{module_path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_package(package_name: str, package_dir: Path) -> ModuleType:
    module = sys.modules.get(package_name)
    if module is not None:
        return module
    package = ModuleType(package_name)
    package.__path__ = [str(package_dir)]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    return package


def _bootstrap_banking_imports() -> None:
    task_suite_pkg_dir = PROJECT_ROOT / "src" / "agentdojo" / "task_suite"
    banking_pkg_dir = PROJECT_ROOT / "src" / "agentdojo" / "default_suites" / "v1" / "banking"

    _ensure_package("agentdojo.task_suite", task_suite_pkg_dir)
    if "agentdojo.task_suite.task_suite" not in sys.modules:
        _load_module(task_suite_pkg_dir / "task_suite.py", "agentdojo.task_suite.task_suite")

    banking_pkg = _ensure_package("agentdojo.default_suites.v1.banking", banking_pkg_dir)
    if "agentdojo.default_suites.v1.banking.task_suite" not in sys.modules:
        banking_task_suite_module = _load_module(
            banking_pkg_dir / "task_suite.py",
            "agentdojo.default_suites.v1.banking.task_suite",
        )
        setattr(banking_pkg, "banking_task_suite", getattr(banking_task_suite_module, "task_suite"))

    if "agentdojo.default_suites.v1.banking.user_tasks" not in sys.modules:
        banking_user_tasks_module = _load_module(
            banking_pkg_dir / "user_tasks.py",
            "agentdojo.default_suites.v1.banking.user_tasks",
        )
        if hasattr(banking_user_tasks_module, "BankingUserTask"):
            setattr(banking_pkg, "BankingUserTask", getattr(banking_user_tasks_module, "BankingUserTask"))

    if "agentdojo.default_suites.v1.banking.injection_tasks_v3" not in sys.modules:
        _load_module(
            banking_pkg_dir / "injection_tasks_v3.py",
            "agentdojo.default_suites.v1.banking.injection_tasks_v3",
        )


def _task_number_from_scenario_name(scenario_name: str) -> int:
    if not (
        scenario_name.startswith("injection_task_")
        or scenario_name.startswith("user_task_")
    ):
        raise ValueError(
            "Scenario name must look like 'user_task_X' or 'injection_task_X', "
            f"got '{scenario_name}'."
        )
    return int(scenario_name.split("_")[-1])


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in '{path}'.")
    return payload


def _load_simulated_user_task_model_output(user_task_class: Any) -> str:
    log_path = DEFAULT_USER_TASK_LOG_ROOT / f"{user_task_class.__name__}.json"
    if not log_path.exists():
        return ""

    payload = _load_json(log_path)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _resolve_summary_path(args: argparse.Namespace, scenario_name: str) -> Path:
    if args.summary_path:
        return Path(args.summary_path).resolve()

    task_number = _task_number_from_scenario_name(scenario_name)
    overall_summary_path = Path(args.summary_root).resolve() / f"UserTask{task_number}" / f"UserTask{task_number}_summary.json"
    overall_summary = _load_json(overall_summary_path)
    # v17 summaries already expose the selected final_tools at the task root,
    # so the top-level summary is sufficient and avoids older nested-path assumptions.
    if overall_summary.get("final_tools"):
        return overall_summary_path

    winning_summary_path = overall_summary.get("winning_summary_path")
    if winning_summary_path:
        return Path(winning_summary_path).resolve()

    candidate_results = overall_summary.get("candidate_results")
    if isinstance(candidate_results, list):
        selected_candidate_id = overall_summary.get("selected_candidate_id")
        for candidate_result in candidate_results:
            if not isinstance(candidate_result, dict):
                continue
            if selected_candidate_id and candidate_result.get("selected_candidate_id") != selected_candidate_id:
                continue
            candidate_summary_path = candidate_result.get("summary_path")
            if candidate_summary_path:
                return Path(candidate_summary_path).resolve()
        for candidate_result in candidate_results:
            if not isinstance(candidate_result, dict):
                continue
            candidate_summary_path = candidate_result.get("summary_path")
            if candidate_summary_path:
                return Path(candidate_summary_path).resolve()

    raise ValueError(
        "Cannot resolve a usable summary path from "
        f"'{overall_summary_path}'. Expected either final_tools, winning_summary_path, "
        "or candidate_results[*].summary_path."
    )


def _resolve_generate_tools_path(summary_path: Path, scenario_name: str) -> Path:
    task_number = _task_number_from_scenario_name(scenario_name)
    generate_tools_filename = f"UserTask{task_number}_generate_tools.json"
    direct_generate_tools_path = summary_path.with_name(generate_tools_filename)
    if direct_generate_tools_path.exists():
        return direct_generate_tools_path

    summary = _load_json(summary_path)
    selected_candidate_summary_path = summary.get("selected_candidate_summary_path")
    if selected_candidate_summary_path:
        candidate_generate_tools_path = Path(selected_candidate_summary_path).resolve().with_name(generate_tools_filename)
        if candidate_generate_tools_path.exists():
            return candidate_generate_tools_path

    raise FileNotFoundError(
        "Cannot find generate tools JSON next to "
        f"'{summary_path}' or its selected candidate summary."
    )


def _resolve_replay_steps(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    final_tools = summary.get("final_tools")
    final_tools_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(final_tools, list):
        for tool in final_tools:
            if isinstance(tool, dict) and isinstance(tool.get("tool_name"), str):
                final_tools_by_name[tool["tool_name"]] = tool

    rollout_results = summary.get("rollout_results")
    if isinstance(rollout_results, list):
        ranked_rollouts = sorted(
            (
                rollout
                for rollout in rollout_results
                if isinstance(rollout, dict) and isinstance(rollout.get("successful_calls"), list)
            ),
            key=lambda rollout: (
                bool(rollout.get("success")),
                bool(rollout.get("coverage_success")),
                len(rollout.get("successful_calls") or []),
            ),
            reverse=True,
        )
        for rollout in ranked_rollouts:
            replay_steps: list[dict[str, Any]] = []
            for call in rollout.get("successful_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("tool_name"), str):
                    continue
                tool_name = call["tool_name"]
                final_tool = final_tools_by_name.get(tool_name, {})
                replay_steps.append(
                    {
                        "tool_name": tool_name,
                        "description": final_tool.get("description", ""),
                        "param": final_tool.get("param"),
                        "required": final_tool.get("required"),
                        "param_values": call.get("args") or {},
                        "simulate_result": final_tool.get("simulate_result"),
                    }
                )
            if replay_steps:
                return replay_steps, "rollout_results.successful_calls"

    if isinstance(final_tools, list) and final_tools:
        return final_tools, "final_tools"

    return [], "missing"


def _resolve_user_task_class(scenario_module: ModuleType):
    user_task_class = getattr(scenario_module, "USER_TASK_CLASS", None)
    if user_task_class is None:
        raise ValueError("Scenario module must expose USER_TASK_CLASS.")
    return user_task_class


def _resolve_injection_task_class(scenario_module: ModuleType, scenario_name: str):
    injection_task_class = getattr(scenario_module, "INJECTION_TASK_CLASS", None)
    if injection_task_class is not None:
        return injection_task_class

    task_number = _task_number_from_scenario_name(scenario_name)
    injection_tasks_module = sys.modules["agentdojo.default_suites.v1.banking.injection_tasks_v3"]
    for attr_name in dir(injection_tasks_module):
        candidate = getattr(injection_tasks_module, attr_name)
        if (
            inspect.isclass(candidate)
            and attr_name.startswith("InjectionTask")
            and getattr(getattr(candidate, "target_task", None), "__name__", None) == f"UserTask{task_number}"
        ):
            return candidate
    raise ValueError(f"Cannot find injection task class for UserTask{task_number}.")


def _resolve_custom_tools(scenario_module: ModuleType) -> list[Any]:
    custom_tools = getattr(scenario_module, "CUSTOM_TOOLS", None)
    if not isinstance(custom_tools, list) or not custom_tools:
        raise ValueError("Scenario module must expose a non-empty CUSTOM_TOOLS list.")
    return custom_tools


def _normalize_description_text(text: str | None) -> str:
    if not text:
        return ""
    kept_lines: list[str] = []
    for raw_line in inspect.cleandoc(text).splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(":param") or stripped.startswith(":return") or stripped.startswith(":raises"):
            break
        if stripped:
            kept_lines.append(stripped)
    return " ".join(kept_lines)


def _validate_tool_name_and_description_stability(
    custom_tools: list[Any],
    expected_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    if len(custom_tools) != len(expected_tools):
        mismatches.append(
            {
                "kind": "tool_count",
                "expected_count": len(expected_tools),
                "actual_count": len(custom_tools),
            }
        )
        return mismatches

    for index, (func, expected_tool) in enumerate(zip(custom_tools, expected_tools, strict=True), start=1):
        expected_name = expected_tool.get("tool_name")
        if func.__name__ != expected_name:
            mismatches.append(
                {
                    "kind": "tool_name",
                    "index": index,
                    "expected": expected_name,
                    "actual": func.__name__,
                }
            )

        expected_description = _normalize_description_text(expected_tool.get("description"))
        actual_description = _normalize_description_text(inspect.getdoc(func))
        if actual_description != expected_description:
            mismatches.append(
                {
                    "kind": "description",
                    "index": index,
                    "tool_name": func.__name__,
                    "expected": expected_description,
                    "actual": actual_description,
                }
            )

    return mismatches


def _load_environment_payload(suite_name: str) -> dict[str, Any]:
    injection_path = SUITES_DIR / suite_name / "injection_vectors.yaml"
    with injection_path.open(encoding="utf-8") as handle:
        raw_vectors = yaml.safe_load(handle)
    defaults = {key: value["default"] for key, value in raw_vectors.items()}

    environment_path = SUITES_DIR / suite_name / "environment.yaml"
    with environment_path.open(encoding="utf-8") as handle:
        loaded_yaml = yaml.load(handle, ImportLoader)
    dumped_yaml = yaml.dump(loaded_yaml, default_flow_style=False)
    rendered_yaml = dumped_yaml.format(**defaults)
    return yaml.safe_load(rendered_yaml)


def _tool_argument_names(func: Any) -> list[str]:
    names: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        if name in BANKING_DEPENDENCY_PARAM_NAMES:
            continue
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        names.append(name)
    return names


def _compatible_kwargs(func: Any, param_values: dict[str, Any]) -> dict[str, Any]:
    accepted = set(_tool_argument_names(func))
    return {key: value for key, value in param_values.items() if key in accepted}


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z]+", (text or "").lower()) if len(token) >= 4}


def _description_overlap_score(func: Any, step: dict[str, Any]) -> int:
    step_tokens = _tokenize(step.get("tool_name", "")) | _tokenize(step.get("description", ""))
    func_tokens = _tokenize(func.__name__) | _tokenize(inspect.getdoc(func) or "") | _tokenize(inspect.getsource(func))
    return len(step_tokens & func_tokens)


def _score_tool_match(
    func: Any,
    step: dict[str, Any],
    step_index: int,
    tool_index: int,
) -> tuple[int, int, int]:
    param_values = step.get("param_values") or {}
    arg_names = set(_tool_argument_names(func))
    provided_keys = set(param_values.keys())
    step_text = f"{step.get('tool_name', '')} {step.get('description', '')}".lower()
    source_text = inspect.getsource(func)

    channel_completion_bonus = 1 if (
        ("channel" in step_text or "channels" in step_text or "membership" in step_text)
        and "add_user_to_channel(" in source_text
    ) else 0
    arg_overlap = len(arg_names & provided_keys)
    accepts_all = 1 if provided_keys.issubset(arg_names) else 0
    semantic_overlap = _description_overlap_score(func, step)
    order_bias = -abs(tool_index - step_index)

    return (
        channel_completion_bonus,
        accepts_all,
        semantic_overlap + arg_overlap + order_bias,
    )


def _resolve_execution_plan(
    summary_steps: list[dict[str, Any]],
    custom_tools: list[Any],
) -> list[dict[str, Any]]:
    used_indices: set[int] = set()
    plan: list[dict[str, Any]] = []

    for step_index, step in enumerate(summary_steps):
        exact_name_candidates: list[tuple[int, Any]] = []
        for tool_index, func in enumerate(custom_tools):
            if tool_index in used_indices:
                continue
            if func.__name__ == step.get("tool_name"):
                exact_name_candidates.append((tool_index, func))

        if exact_name_candidates:
            selected_index, selected_func = exact_name_candidates[0]
            used_indices.add(selected_index)
            plan.append(
                {
                    "summary_tool_name": step.get("tool_name"),
                    "scenario_tool_name": selected_func.__name__,
                    "provided_kwargs": step.get("param_values") or {},
                    "runtime_kwargs": _compatible_kwargs(selected_func, step.get("param_values") or {}),
                }
            )
            continue

        candidates: list[tuple[tuple[int, int, int], int, Any]] = []
        for tool_index, func in enumerate(custom_tools):
            if tool_index in used_indices:
                continue
            score = _score_tool_match(func, step, step_index, tool_index)
            if score[0] == 0 and score[1] == 0 and score[2] < 0:
                continue
            candidates.append((score, tool_index, func))

        candidates.sort(reverse=True)
        selected_index: int | None = None
        selected_func: Any | None = None

        if candidates and candidates[0][0][0] > 0:
            _, selected_index, selected_func = candidates[0]
        elif candidates and candidates[0][0][1] > 0:
            _, selected_index, selected_func = candidates[0]
        elif candidates and len(custom_tools) == len(summary_steps):
            _, selected_index, selected_func = candidates[0]
        else:
            raise ValueError(f"Cannot match summary tool '{step.get('tool_name')}' to a scenario tool.")

        if selected_index is not None:
            used_indices.add(selected_index)

        assert selected_func is not None
        plan.append(
            {
                "summary_tool_name": step.get("tool_name"),
                "scenario_tool_name": selected_func.__name__,
                "provided_kwargs": step.get("param_values") or {},
                "runtime_kwargs": _compatible_kwargs(selected_func, step.get("param_values") or {}),
            }
        )

    return plan


def _normalize_output(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_normalize_output(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_output(item) for key, item in value.items()}
    return value


def _required_argument_names(func: Any) -> list[str]:
    required: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        if name in BANKING_DEPENDENCY_PARAM_NAMES:
            continue
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return required


def _all_callable_argument_names(func: Any) -> list[str]:
    names: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        names.append(name)
    return names


def _summary_param_specs(step: dict[str, Any]) -> dict[str, str | None]:
    param_schema = step.get("param")
    if isinstance(param_schema, list):
        return {
            item["name"]: item.get("annotation")
            for item in param_schema
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
    return {name: None for name in (step.get("param_values") or {}).keys()}


def _function_param_annotations(func: Any) -> dict[str, str | None]:
    source = textwrap.dedent(inspect.getsource(func))
    module = ast.parse(source)
    function_node = next(
        node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    annotations: dict[str, str | None] = {}
    params = [
        *function_node.args.posonlyargs,
        *function_node.args.args,
        *function_node.args.kwonlyargs,
    ]
    if function_node.args.vararg is not None:
        params.append(function_node.args.vararg)
    if function_node.args.kwarg is not None:
        params.append(function_node.args.kwarg)
    for param in params:
        if param.annotation is None:
            annotations[param.arg] = None
            continue
        annotations[param.arg] = ast.get_source_segment(source, param.annotation) or ast.unparse(param.annotation)
    return annotations


def _function_depends_param_names(func: Any) -> set[str]:
    source = textwrap.dedent(inspect.getsource(func))
    module = ast.parse(source)
    function_node = next(
        node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    depends_names: set[str] = set()
    params = [
        *function_node.args.posonlyargs,
        *function_node.args.args,
        *function_node.args.kwonlyargs,
    ]
    if function_node.args.vararg is not None:
        params.append(function_node.args.vararg)
    if function_node.args.kwarg is not None:
        params.append(function_node.args.kwarg)

    for param in params:
        if param.annotation is None or param.arg not in BANKING_DEPENDENCY_PARAM_NAMES:
            continue
        annotation = ast.get_source_segment(source, param.annotation) or ast.unparse(param.annotation)
        normalized = re.sub(r"\s+", "", annotation)
        if normalized.startswith("Annotated[") and "Depends(" in normalized:
            depends_names.add(param.arg)

    return depends_names


def _normalize_annotation(annotation: str | None) -> str | None:
    if annotation is None:
        return None
    return re.sub(r"\s+", "", annotation).replace("'", '"')


def _validate_param_signature_match(func: Any, step: dict[str, Any]) -> str | None:
    summary_param_specs = _summary_param_specs(step)
    summary_param_names = set(summary_param_specs)
    function_param_names = set(_all_callable_argument_names(func))
    depends_param_names = _function_depends_param_names(func)
    extra_function_params = function_param_names - summary_param_names
    illegal_extra_function_params = sorted(extra_function_params - depends_param_names)
    missing_in_function = sorted(summary_param_names - function_param_names)
    if missing_in_function or illegal_extra_function_params:
        return (
            f"Param mismatch for tool `{func.__name__}`: "
            f"summary params={sorted(summary_param_names)}, "
            f"function params={sorted(function_param_names)}, "
            f"missing_in_function={missing_in_function}, "
            f"illegal_extra_function_params={illegal_extra_function_params}, "
            f"allowed_extra_depends={sorted(depends_param_names)}"
        )

    function_annotations = _function_param_annotations(func)
    annotation_mismatches = []
    for name, expected_annotation in sorted(summary_param_specs.items()):
        if expected_annotation is None:
            continue
        actual_annotation = function_annotations.get(name)
        if _normalize_annotation(actual_annotation) != _normalize_annotation(expected_annotation):
            annotation_mismatches.append(
                {
                    "name": name,
                    "summary_annotation": expected_annotation,
                    "function_annotation": actual_annotation,
                }
            )
    if not annotation_mismatches:
        return None

    return f"Param annotation mismatch for tool `{func.__name__}`: {annotation_mismatches}"


def _collect_primitive_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, bool):
        return {repr(value)}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {repr(value), str(value)}
    if isinstance(value, dict):
        values: set[str] = set()
        for nested_value in value.values():
            values.update(_collect_primitive_values(nested_value))
        return values
    if isinstance(value, (list, tuple, set)):
        values: set[str] = set()
        for nested_value in value:
            values.update(_collect_primitive_values(nested_value))
        return values
    return set()


def _collect_forbidden_ground_truth_arg_values(injection_task: Any, pre_env: TaskEnvironment) -> set[str]:
    values: set[str] = set()
    for function_call in injection_task.ground_truth(pre_env):
        values.update(_collect_primitive_values(getattr(function_call, "args", {})))
    allowed_target_values = _collect_primitive_values(getattr(injection_task, "_TARGET", None))
    return {
        value
        for value in values
        if value != "" and value not in allowed_target_values
    }


class _FunctionLiteralCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.values: set[str] = set()
        self.docstring_constant_ids: set[int] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self.docstring_constant_ids:
            return
        if isinstance(node.value, str):
            self.values.add(node.value)
        elif isinstance(node.value, bool):
            self.values.add(repr(node.value))
        elif isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            self.values.add(repr(node.value))
            self.values.add(str(node.value))


def _find_ground_truth_arg_leaks(
    custom_tools: list[Any],
    forbidden_values: set[str],
) -> list[dict[str, Any]]:
    if not forbidden_values:
        return []

    leaks: list[dict[str, Any]] = []
    for func in custom_tools:
        source = textwrap.dedent(inspect.getsource(func))
        module = ast.parse(source)
        collector = _FunctionLiteralCollector()
        collector.visit(module)
        matched_values = sorted(value for value in collector.values if value in forbidden_values)
        if matched_values:
            leaks.append(
                {
                    "tool_name": func.__name__,
                    "matched_values": matched_values,
                }
            )
    return leaks


def _execute_tool(func: Any, env: TaskEnvironment, runtime_kwargs: dict[str, Any]) -> tuple[Any, str | None]:
    try:
        call_kwargs = dict(runtime_kwargs)
        signature = inspect.signature(func)
        annotations = _function_param_annotations(func)
        if "banking" in signature.parameters:
            call_kwargs["banking"] = env
        if "filesystem" in signature.parameters:
            call_kwargs["filesystem"] = env.filesystem
        if "bank_account" in signature.parameters:
            call_kwargs["bank_account"] = env.bank_account
        if "user_account" in signature.parameters:
            call_kwargs["user_account"] = env.user_account
        if "account" in signature.parameters:
            account_annotation = _normalize_annotation(annotations.get("account")) or ""
            if 'Depends("bank_account")' in account_annotation:
                call_kwargs["account"] = env.bank_account
            elif 'Depends("user_account")' in account_annotation:
                call_kwargs["account"] = env.user_account
        result = func(**call_kwargs)
        return result, None
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def _resolve_call_kwargs(
    func: Any,
    provided_kwargs: dict[str, Any],
    context_kwargs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    primary_kwargs = _compatible_kwargs(func, provided_kwargs)
    accepted = set(_tool_argument_names(func))

    contextual_kwargs = {key: value for key, value in context_kwargs.items() if key in accepted}
    fallback_kwargs = dict(contextual_kwargs)
    fallback_kwargs.update({key: value for key, value in primary_kwargs.items() if key not in contextual_kwargs})

    required = set(_required_argument_names(func))
    if required.issubset(primary_kwargs.keys()):
        return primary_kwargs, fallback_kwargs if fallback_kwargs != primary_kwargs else None
    if fallback_kwargs != primary_kwargs:
        return fallback_kwargs, primary_kwargs if primary_kwargs else None
    return primary_kwargs, None


def _evaluate_user_task(
    user_task: Any,
    pre_env: TaskEnvironment,
    post_env: TaskEnvironment,
    traces: list[FunctionCall],
    model_output: str,
) -> tuple[bool | None, str, str | None]:
    try:
        utility_from_traces = user_task.utility_from_traces(
            model_output=model_output,
            pre_environment=pre_env,
            post_environment=post_env,
            traces=traces,
        )
        if utility_from_traces is not None:
            return bool(utility_from_traces), "utility_from_traces", None
    except Exception as exc:
        utility_from_traces_error = f"{type(exc).__name__}: {exc}"
    else:
        utility_from_traces_error = None

    try:
        utility = user_task.utility(
            model_output=model_output,
            pre_environment=pre_env,
            post_environment=post_env,
            strict=False,
        )
        return bool(utility), "utility", utility_from_traces_error
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if utility_from_traces_error:
            error = f"{utility_from_traces_error}; utility_error={error}"
        return None, "not_evaluated", error


def _evaluate_injection_task(
    injection_task: Any,
    pre_env: TaskEnvironment,
    post_env: TaskEnvironment,
    traces: list[FunctionCall],
    model_output: str,
) -> tuple[bool | None, str, str | None]:
    try:
        security_from_traces = injection_task.security_from_traces(
            model_output=model_output,
            pre_environment=pre_env,
            post_environment=post_env,
            traces=traces,
        )
        if security_from_traces is not None:
            return bool(security_from_traces), "security_from_traces", None
    except Exception as exc:
        security_from_traces_error = f"{type(exc).__name__}: {exc}"
    else:
        security_from_traces_error = None

    try:
        security = injection_task.security(
            model_output=model_output,
            pre_environment=pre_env,
            post_environment=post_env,
        )
        return bool(security), "security", security_from_traces_error
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if security_from_traces_error:
            error = f"{security_from_traces_error}; security_error={error}"
        return None, "not_evaluated", error


def _write_log(log_dir: Path, scenario_name: str, payload: dict[str, Any]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{scenario_name}.json"
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return log_path


def main() -> None:
    args = _parse_args()
    _bootstrap_banking_imports()
    from agentdojo.default_suites.v1.banking.task_suite import BankingEnvironment

    scenario_path = _resolve_scenario_path(args.scenario)
    scenario_module = _load_module(scenario_path)
    scenario_name = scenario_path.stem
    summary_path = _resolve_summary_path(args, scenario_name)
    generate_tools_path = _resolve_generate_tools_path(summary_path, scenario_name)
    summary = _load_json(summary_path)
    expected_generated_tools = _load_json_list(generate_tools_path)

    user_task_class = _resolve_user_task_class(scenario_module)
    injection_task_class = _resolve_injection_task_class(scenario_module, scenario_name)
    custom_tools = _resolve_custom_tools(scenario_module)
    tool_metadata_mismatches = _validate_tool_name_and_description_stability(
        custom_tools,
        expected_generated_tools,
    )
    env = BankingEnvironment.model_validate(_load_environment_payload("banking"))
    pre_env = env.model_copy(deep=True)
    user_task = user_task_class()
    injection_task = injection_task_class()
    simulated_user_task_model_output = _load_simulated_user_task_model_output(user_task_class)
    forbidden_ground_truth_arg_values = _collect_forbidden_ground_truth_arg_values(injection_task, pre_env)
    ground_truth_arg_leaks = _find_ground_truth_arg_leaks(custom_tools, forbidden_ground_truth_arg_values)

    summary_steps, summary_step_source = _resolve_replay_steps(summary)
    if not summary_steps:
        raise ValueError(f"No replayable steps found in '{summary_path}'.")

    execution_plan = _resolve_execution_plan(summary_steps, custom_tools)

    traces: list[FunctionCall] = []
    execution_log: list[dict[str, Any]] = []
    metadata_error: str | None = None
    if tool_metadata_mismatches:
        metadata_error = (
            "Scenario tool name/description changed from UserTask generate_tools JSON: "
            f"{tool_metadata_mismatches}"
        )
    leak_error: str | None = None
    if ground_truth_arg_leaks:
        leak_error = (
            "InjectionTask ground_truth arg values leaked into generated tool functions: "
            f"{ground_truth_arg_leaks}"
        )
    fatal_error: str | None = metadata_error or leak_error
    context_kwargs: dict[str, Any] = {}

    for step, plan_item in zip(summary_steps, execution_plan, strict=True):
        if fatal_error is not None:
            break
        scenario_tool = next(tool for tool in custom_tools if tool.__name__ == plan_item["scenario_tool_name"])
        param_mismatch_error = _validate_param_signature_match(
            scenario_tool,
            step,
        )
        if param_mismatch_error is not None:
            fatal_error = param_mismatch_error
            execution_log.append(
                {
                    "summary_tool_name": plan_item["summary_tool_name"],
                    "scenario_tool_name": plan_item["scenario_tool_name"],
                    "provided_kwargs": plan_item["provided_kwargs"],
                    "runtime_kwargs": {},
                    "fallback_kwargs": None,
                    "error": param_mismatch_error,
                    "result": None,
                }
            )
            break
        primary_kwargs, fallback_kwargs = _resolve_call_kwargs(
            scenario_tool,
            plan_item["provided_kwargs"],
            context_kwargs,
        )
        result, error = _execute_tool(scenario_tool, env, primary_kwargs)
        used_kwargs = primary_kwargs
        if error is not None and fallback_kwargs is not None:
            retry_result, retry_error = _execute_tool(scenario_tool, env, fallback_kwargs)
            if retry_error is None:
                result, error = retry_result, retry_error
                used_kwargs = fallback_kwargs
        traces.append(FunctionCall(function=plan_item["scenario_tool_name"], args=used_kwargs))
        context_kwargs.update({key: value for key, value in used_kwargs.items()})
        context_kwargs.update(
            {
                key: value
                for key, value in (step.get("param_values") or {}).items()
                if key not in context_kwargs
            }
        )
        execution_log.append(
            {
                "summary_tool_name": plan_item["summary_tool_name"],
                "scenario_tool_name": plan_item["scenario_tool_name"],
                "provided_kwargs": plan_item["provided_kwargs"],
                "runtime_kwargs": used_kwargs,
                "fallback_kwargs": fallback_kwargs,
                "error": error,
                "result": _normalize_output(result),
            }
        )
        if error is not None:
            fatal_error = error
            break

    if metadata_error is not None:
        utility, utility_eval_method, utility_error = False, "metadata_validation", metadata_error
        security, security_eval_method, security_error = False, "metadata_validation", metadata_error
    else:
        utility, utility_eval_method, utility_error = _evaluate_user_task(
            user_task,
            pre_env,
            env,
            traces,
            simulated_user_task_model_output,
        )
        security, security_eval_method, security_error = _evaluate_injection_task(
            injection_task,
            pre_env,
            env,
            traces,
            simulated_user_task_model_output,
        )

    payload = {
        "scenario": scenario_name,
        "scenario_path": str(scenario_path),
        "summary_path": str(summary_path),
        "generate_tools_path": str(generate_tools_path),
        "replay_step_source": summary_step_source,
        "task_name": summary.get("task_name"),
        "user_task": user_task_class.__name__,
        "injection_task": injection_task_class.__name__,
        "simulated_user_task_model_output": simulated_user_task_model_output,
        "target_tool_count": summary.get("target_tool_count"),
        "steps_requested": len(summary_steps),
        "steps_executed": len(execution_log),
        "execution_success": fatal_error is None,
        "execution_error": fatal_error,
        "tool_metadata_mismatches": tool_metadata_mismatches,
        "forbidden_ground_truth_arg_values": sorted(forbidden_ground_truth_arg_values),
        "ground_truth_arg_leaks": ground_truth_arg_leaks,
        "utility": utility,
        "utility_eval_method": utility_eval_method,
        "utility_error": utility_error,
        "security": security,
        "security_eval_method": security_eval_method,
        "security_error": security_error,
        "execution_plan": execution_plan,
        "execution_log": execution_log,
    }
    log_path = _write_log(Path(args.log_dir).resolve(), scenario_name, payload)

    print(f"Scenario: {scenario_name}")
    print(f"Summary: {summary_path}")
    print(f"Executed steps: {len(execution_log)}/{len(summary_steps)}")
    print(f"Execution success: {fatal_error is None}")
    if fatal_error:
        print(f"Execution error: {fatal_error}")
    if tool_metadata_mismatches:
        print(f"Tool metadata mismatch: {tool_metadata_mismatches}")
    if ground_truth_arg_leaks:
        print(f"Ground-truth arg leak: {ground_truth_arg_leaks}")
    print(f"User task utility: {utility}")
    print(f"Utility eval method: {utility_eval_method}")
    if utility_error:
        print(f"Utility eval detail: {utility_error}")
    print(f"Injection security: {security}")
    print(f"Security eval method: {security_eval_method}")
    if security_error:
        print(f"Security eval detail: {security_error}")
    print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    main()
