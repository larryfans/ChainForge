"""
Code parser: extract tool functions from an existing scenario module.

Given a .py file like bank_injection0.py, parse out each tool function's
code body, description, and schema so we can evaluate and refine them.
"""

import ast
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolCode:
    """Parsed representation of one tool function."""
    tool_id: str           # T1, T2, ...
    name: str              # function name
    description: str       # docstring (first paragraph)
    params: dict[str, str] # param_name -> type annotation string
    return_type: str
    code: str              # full function source (def ... to end)
    line_start: int
    line_end: int


@dataclass
class ParsedScenario:
    """All information extracted from a scenario module."""
    scenario_id: str
    source: str                         # full file source
    tools: list[ToolCode]
    user_task_class: str
    injection_task_class: str
    user_prompt_expr: str               # e.g. "UserTask5.PROMPT"
    imports_code: str                   # import block
    footer_code: str                    # CUSTOM_TOOLS, get_tools, etc.
    include_base_tools: bool = True     # preserved from original


def parse_scenario(path: str | Path) -> ParsedScenario:
    """
    Parse an existing scenario .py file into structured components.

    Args:
        path: Path to the scenario module

    Returns:
        ParsedScenario with all tool code extracted
    """
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    scenario_id = path.stem

    # --- Extract imports block ---
    import_lines = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_lines.append(ast.get_source_segment(source, node) or "")

    imports_code = "\n".join(import_lines)

    # --- Identify tool functions ---
    # Tool functions: public (no leading _), not get_tools
    all_funcs = [n for n in ast.iter_child_nodes(tree) if isinstance(n, ast.FunctionDef)]

    tool_funcs = []
    for func in all_funcs:
        if not func.name.startswith("_") and func.name != "get_tools":
            tool_funcs.append(func)

    # --- Extract tool code ---
    tools: list[ToolCode] = []
    for idx, func in enumerate(tool_funcs):
        tc = _extract_tool_code(func, source, lines, idx + 1)
        tools.append(tc)

    # --- Extract module-level assignments ---
    user_task_class = ""
    injection_task_class = ""
    user_prompt_expr = ""
    include_base_tools = True

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "USER_TASK_CLASS":
                        user_task_class = ast.get_source_segment(source, node.value) or ""
                    elif target.id == "INJECTION_TASK_CLASS":
                        injection_task_class = ast.get_source_segment(source, node.value) or ""
                    elif target.id == "user_prompt":
                        user_prompt_expr = ast.get_source_segment(source, node.value) or ""
                    elif target.id == "INCLUDE_BASE_TOOLS":
                        if isinstance(node.value, ast.Constant):
                            include_base_tools = bool(node.value.value)

    # --- Extract footer (get_tools + CUSTOM_TOOLS) ---
    footer_parts = []
    for func in all_funcs:
        if func.name == "get_tools":
            footer_parts.append(
                _get_source_lines(lines, func.lineno, func.end_lineno or func.lineno)
            )
    # Add CUSTOM_TOOLS assignment
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CUSTOM_TOOLS":
                    footer_parts.append(ast.get_source_segment(source, node) or "")

    footer_code = "\n\n".join(footer_parts)

    return ParsedScenario(
        scenario_id=scenario_id,
        source=source,
        tools=tools,
        user_task_class=user_task_class,
        injection_task_class=injection_task_class,
        user_prompt_expr=user_prompt_expr,
        imports_code=imports_code,
        footer_code=footer_code,
        include_base_tools=include_base_tools,
    )


def reassemble_module(scenario: ParsedScenario) -> str:
    """
    Reassemble a complete .py module from a ParsedScenario.
    Call this after modifying tool code in scenario.tools[i].code.
    """
    parts = []

    # Imports
    parts.append(scenario.imports_code)
    parts.append("")

    # Module-level assignments — class assignments before user_prompt (which references them)
    parts.append(f"INCLUDE_BASE_TOOLS = {scenario.include_base_tools}")
    parts.append(f"USER_TASK_CLASS = {scenario.user_task_class}")
    parts.append(f"INJECTION_TASK_CLASS = {scenario.injection_task_class}")
    parts.append(f"user_prompt = {scenario.user_prompt_expr}")
    parts.append("")

    # Tool functions
    for tool in scenario.tools:
        parts.append(tool.code)
        parts.append("")

    # Footer
    if scenario.footer_code.strip():
        parts.append(scenario.footer_code)

    return "\n".join(parts)


def _extract_tool_code(
    func: ast.FunctionDef,
    source: str,
    lines: list[str],
    index: int,
) -> ToolCode:
    """Extract a ToolCode from a function AST node."""

    # Full function source
    code = _get_source_lines(lines, func.lineno, func.end_lineno or func.lineno)

    # Docstring
    description = ""
    if (func.body
            and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
            and isinstance(func.body[0].value.value, str)):
        raw_doc = func.body[0].value.value.strip()
        description = raw_doc.split("\n\n")[0].replace("\n", " ").strip()

    # Parameters
    params: dict[str, str] = {}
    for arg in func.args.args:
        ann = ""
        if arg.annotation:
            ann = ast.get_source_segment(source, arg.annotation) or "Any"
        params[arg.arg] = ann

    # Defaults
    defaults = func.args.defaults
    n_defaults = len(defaults)
    n_args = len(func.args.args)
    for i, default in enumerate(defaults):
        arg_idx = n_args - n_defaults + i
        arg_name = func.args.args[arg_idx].arg
        default_str = ast.get_source_segment(source, default) or ""
        if default_str:
            params[arg_name] = f"{params[arg_name]} = {default_str}"

    # Return type
    return_type = "Any"
    if func.returns:
        return_type = ast.get_source_segment(source, func.returns) or "Any"

    return ToolCode(
        tool_id=f"T{index}",
        name=func.name,
        description=description,
        params=params,
        return_type=return_type,
        code=code,
        line_start=func.lineno,
        line_end=func.end_lineno or func.lineno,
    )


def _get_source_lines(lines: list[str], start: int, end: int) -> str:
    """Get source lines (1-indexed, inclusive)."""
    return "".join(lines[start - 1 : end]).rstrip()
