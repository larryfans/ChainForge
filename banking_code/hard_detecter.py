#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_DIR = PROJECT_ROOT / "src" / "banking_code"
ALLOWED_TOP_LEVEL_ASSIGNMENTS = {
    "user_prompt",
    "USER_TASK_CLASS",
    "INJECTION_TASK_CLASS",
    "INCLUDE_BASE_TOOLS",
    "CUSTOM_TOOLS",
}
ALLOWED_USER_TASK_FIELDS = {"PROMPT"}
FORBIDDEN_FUNCTION_SCOPE_ASSIGNMENTS = {
    "user_prompt",
    "USER_TASK_CLASS",
    "INJECTION_TASK_CLASS",
    "INCLUDE_BASE_TOOLS",
}
FORBIDDEN_DYNAMIC_CONFIG_LOOKUPS = {
    "USER_TASK_CLASS",
    "INJECTION_TASK_CLASS",
}
FORBIDDEN_DYNAMIC_METHOD_MUTATIONS = {
    "utility_from_traces",
    "security_from_traces",
}


@dataclass
class Violation:
    path: Path
    line: int
    column: int
    rule: str
    detail: str


@dataclass
class UserTaskMetadata:
    fields: set[str]
    literal_values: set[object]


@dataclass
class InjectionTaskMetadata:
    target_value: str | None
    ground_truth_string_values: set[str]


@dataclass
class UserTaskGroundTruthMetadata:
    ground_truth_string_values: set[str]


def resolve_module_file(module_name: str) -> Path | None:
    module_path = PROJECT_ROOT / "src" / Path(module_name.replace(".", "/"))
    py_file = module_path.with_suffix(".py")
    if py_file.exists():
        return py_file
    init_file = module_path / "__init__.py"
    if init_file.exists():
        return init_file
    return None


def extract_assigned_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(extract_assigned_names(elt))
        return names
    return []


def extract_assignment_targets(target: ast.AST) -> list[ast.AST]:
    if isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        targets: list[ast.AST] = []
        for elt in target.elts:
            targets.extend(extract_assignment_targets(elt))
        return targets
    return []


def is_forbidden_injection_target_reference(node: ast.Attribute) -> bool:
    return (
        node.attr == "_TARGET"
        and isinstance(node.value, ast.Name)
        and (
            node.value.id == "INJECTION_TASK_CLASS"
            or node.value.id.startswith("InjectionTask")
        )
    )


def is_forbidden_function_scope_base_name(name: str) -> bool:
    return (
        name in {"USER_TASK_CLASS", "INJECTION_TASK_CLASS", "INCLUDE_BASE_TOOLS"}
        or name.startswith("UserTask")
        or name.startswith("InjectionTask")
    )


def get_forbidden_function_scope_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    if isinstance(current, ast.Name) and is_forbidden_function_scope_base_name(current.id):
        return current.id
    return None


def extract_string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def get_dynamic_namespace_lookup_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    if not isinstance(node.value, ast.Call):
        return None
    call = node.value
    if not isinstance(call.func, ast.Name) or call.func.id not in {"globals", "locals", "vars"}:
        return None
    if call.args or call.keywords:
        return None
    key = extract_string_constant(node.slice)
    if key in FORBIDDEN_DYNAMIC_CONFIG_LOOKUPS:
        return key
    return None


def collect_class_metadata(module_path: Path, class_name: str) -> UserTaskMetadata:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    fields: set[str] = set()
    literal_values: set[object] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    target_names = extract_assigned_names(target)
                    fields.update(target_names)
                    if len(target_names) == 1:
                        try:
                            literal_values.add(ast.literal_eval(stmt.value))
                        except Exception:
                            pass
            elif isinstance(stmt, ast.AnnAssign):
                target_names = extract_assigned_names(stmt.target)
                fields.update(target_names)
                if len(target_names) == 1 and stmt.value is not None:
                    try:
                        literal_values.add(ast.literal_eval(stmt.value))
                    except Exception:
                        pass
            elif isinstance(stmt, ast.AugAssign):
                fields.update(extract_assigned_names(stmt.target))
        break
    return UserTaskMetadata(fields=fields, literal_values=literal_values)


def load_sensitive_user_task_metadata(
    tree: ast.Module,
) -> tuple[UserTaskMetadata, str | None]:
    import_map: dict[str, str] = {}
    user_task_class_name: str | None = None

    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                local_name = alias.asname or alias.name
                import_map[local_name] = stmt.module

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "USER_TASK_CLASS":
                if isinstance(stmt.value, ast.Name):
                    user_task_class_name = stmt.value.id
                break
        if user_task_class_name is not None:
            break

    if user_task_class_name is None:
        return UserTaskMetadata(fields=set(), literal_values=set()), None

    module_name = import_map.get(user_task_class_name)
    if module_name is None:
        return UserTaskMetadata(fields=set(), literal_values=set()), user_task_class_name

    module_path = resolve_module_file(module_name)
    if module_path is None:
        return UserTaskMetadata(fields=set(), literal_values=set()), user_task_class_name

    metadata = collect_class_metadata(module_path, user_task_class_name)
    metadata.fields -= ALLOWED_USER_TASK_FIELDS
    if "PROMPT" in metadata.fields:
        metadata.fields.remove("PROMPT")
    metadata.literal_values.discard(None)
    try:
        metadata.literal_values.remove(ast.literal_eval(ast.parse("None").body[0].value))
    except Exception:
        pass
    return metadata, user_task_class_name


def extract_string_literals_from_resolved_value(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(extract_string_literals_from_resolved_value(item))
        return result
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            result.update(extract_string_literals_from_resolved_value(key))
            result.update(extract_string_literals_from_resolved_value(item))
        return result
    return set()


def extract_param_string_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(extract_param_string_values(item))
        return result
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(extract_param_string_values(item))
        return result
    return set()


class GroundTruthValueResolver:
    def __init__(
        self,
        class_literal_values: dict[str, object],
        local_values: dict[str, object],
    ) -> None:
        self.class_literal_values = class_literal_values
        self.local_values = local_values

    def resolve(self, node: ast.AST) -> object | None:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.local_values.get(node.id)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return self.class_literal_values.get(node.attr)
            return None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    continue
                if isinstance(value, ast.FormattedValue):
                    resolved = self.resolve(value.value)
                    if isinstance(resolved, str):
                        parts.append(resolved)
                        continue
                return None
            return "".join(parts)
        if isinstance(node, ast.List):
            items = []
            for elt in node.elts:
                resolved = self.resolve(elt)
                if resolved is None:
                    return None
                items.append(resolved)
            return items
        if isinstance(node, ast.Tuple):
            items = []
            for elt in node.elts:
                resolved = self.resolve(elt)
                if resolved is None:
                    return None
                items.append(resolved)
            return tuple(items)
        if isinstance(node, ast.Set):
            items = []
            for elt in node.elts:
                resolved = self.resolve(elt)
                if resolved is None:
                    return None
                items.append(resolved)
            return set(items)
        if isinstance(node, ast.Dict):
            resolved_dict: dict[object, object] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    return None
                resolved_key = self.resolve(key_node)
                resolved_value = self.resolve(value_node)
                if resolved_key is None or resolved_value is None:
                    return None
                resolved_dict[resolved_key] = resolved_value
            return resolved_dict
        return None


def collect_injection_class_metadata(module_path: Path, class_name: str) -> InjectionTaskMetadata:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    class_node: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_node = node
            break

    if class_node is None:
        return InjectionTaskMetadata(target_value=None, ground_truth_string_values=set())

    class_literal_values: dict[str, object] = {}
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                target_names = extract_assigned_names(target)
                if len(target_names) != 1:
                    continue
                try:
                    class_literal_values[target_names[0]] = ast.literal_eval(stmt.value)
                except Exception:
                    continue
        elif isinstance(stmt, ast.AnnAssign):
            target_names = extract_assigned_names(stmt.target)
            if len(target_names) != 1 or stmt.value is None:
                continue
            try:
                class_literal_values[target_names[0]] = ast.literal_eval(stmt.value)
            except Exception:
                continue

    ground_truth_node: ast.FunctionDef | None = None
    for stmt in class_node.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "ground_truth":
            ground_truth_node = stmt
            break

    target_value = class_literal_values.get("_TARGET")
    if not isinstance(target_value, str):
        target_value = None

    if ground_truth_node is None:
        return InjectionTaskMetadata(target_value=target_value, ground_truth_string_values=set())

    local_values: dict[str, object] = {}
    resolver = GroundTruthValueResolver(class_literal_values, local_values)
    for stmt in ast.walk(ground_truth_node):
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                continue
            target_names = extract_assigned_names(stmt.targets[0])
            if len(target_names) != 1:
                continue
            resolved = resolver.resolve(stmt.value)
            if resolved is not None:
                local_values[target_names[0]] = resolved
        elif isinstance(stmt, ast.AnnAssign):
            target_names = extract_assigned_names(stmt.target)
            if len(target_names) != 1 or stmt.value is None:
                continue
            resolved = resolver.resolve(stmt.value)
            if resolved is not None:
                local_values[target_names[0]] = resolved

    string_values: set[str] = set()
    for stmt in ast.walk(ground_truth_node):
        if not isinstance(stmt, ast.Call):
            continue
        if not isinstance(stmt.func, ast.Name) or stmt.func.id != "FunctionCall":
            continue
        for keyword in stmt.keywords:
            if keyword.arg not in {"args", "placeholder_args"}:
                continue
            resolved = resolver.resolve(keyword.value)
            if resolved is None:
                continue
            string_values.update(extract_param_string_values(resolved))

    return InjectionTaskMetadata(
        target_value=target_value,
        ground_truth_string_values=string_values,
    )


def collect_user_task_ground_truth_metadata(
    module_path: Path,
    class_name: str,
) -> UserTaskGroundTruthMetadata:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    class_node: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_node = node
            break

    if class_node is None:
        return UserTaskGroundTruthMetadata(ground_truth_string_values=set())

    class_literal_values: dict[str, object] = {}
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                target_names = extract_assigned_names(target)
                if len(target_names) != 1:
                    continue
                try:
                    class_literal_values[target_names[0]] = ast.literal_eval(stmt.value)
                except Exception:
                    continue
        elif isinstance(stmt, ast.AnnAssign):
            target_names = extract_assigned_names(stmt.target)
            if len(target_names) != 1 or stmt.value is None:
                continue
            try:
                class_literal_values[target_names[0]] = ast.literal_eval(stmt.value)
            except Exception:
                continue

    ground_truth_node: ast.FunctionDef | None = None
    for stmt in class_node.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "ground_truth":
            ground_truth_node = stmt
            break

    if ground_truth_node is None:
        return UserTaskGroundTruthMetadata(ground_truth_string_values=set())

    local_values: dict[str, object] = {}
    resolver = GroundTruthValueResolver(class_literal_values, local_values)
    for stmt in ast.walk(ground_truth_node):
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                continue
            target_names = extract_assigned_names(stmt.targets[0])
            if len(target_names) != 1:
                continue
            resolved = resolver.resolve(stmt.value)
            if resolved is not None:
                local_values[target_names[0]] = resolved
        elif isinstance(stmt, ast.AnnAssign):
            target_names = extract_assigned_names(stmt.target)
            if len(target_names) != 1 or stmt.value is None:
                continue
            resolved = resolver.resolve(stmt.value)
            if resolved is not None:
                local_values[target_names[0]] = resolved

    string_values: set[str] = set()
    for stmt in ast.walk(ground_truth_node):
        if not isinstance(stmt, ast.Call):
            continue
        if not isinstance(stmt.func, ast.Name) or stmt.func.id != "FunctionCall":
            continue
        for keyword in stmt.keywords:
            if keyword.arg not in {"args", "placeholder_args"}:
                continue
            resolved = resolver.resolve(keyword.value)
            if resolved is None:
                continue
            string_values.update(extract_param_string_values(resolved))

    return UserTaskGroundTruthMetadata(ground_truth_string_values=string_values)


def load_user_task_ground_truth_metadata(
    tree: ast.Module,
) -> tuple[UserTaskGroundTruthMetadata, str | None]:
    import_map: dict[str, str] = {}
    user_task_class_name: str | None = None

    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                local_name = alias.asname or alias.name
                import_map[local_name] = stmt.module

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "USER_TASK_CLASS":
                if isinstance(stmt.value, ast.Name):
                    user_task_class_name = stmt.value.id
                break
        if user_task_class_name is not None:
            break

    if user_task_class_name is None:
        return UserTaskGroundTruthMetadata(ground_truth_string_values=set()), None

    module_name = import_map.get(user_task_class_name)
    if module_name is None:
        return UserTaskGroundTruthMetadata(ground_truth_string_values=set()), user_task_class_name

    module_path = resolve_module_file(module_name)
    if module_path is None:
        return UserTaskGroundTruthMetadata(ground_truth_string_values=set()), user_task_class_name

    return collect_user_task_ground_truth_metadata(module_path, user_task_class_name), user_task_class_name


def load_injection_task_metadata(
    tree: ast.Module,
) -> tuple[InjectionTaskMetadata, str | None]:
    import_map: dict[str, str] = {}
    injection_task_class_name: str | None = None

    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                local_name = alias.asname or alias.name
                import_map[local_name] = stmt.module

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "INJECTION_TASK_CLASS":
                if isinstance(stmt.value, ast.Name):
                    injection_task_class_name = stmt.value.id
                break
        if injection_task_class_name is not None:
            break

    if injection_task_class_name is None:
        return InjectionTaskMetadata(target_value=None, ground_truth_string_values=set()), None

    module_name = import_map.get(injection_task_class_name)
    if module_name is None:
        return InjectionTaskMetadata(target_value=None, ground_truth_string_values=set()), injection_task_class_name

    module_path = resolve_module_file(module_name)
    if module_path is None:
        return InjectionTaskMetadata(target_value=None, ground_truth_string_values=set()), injection_task_class_name

    return collect_injection_class_metadata(module_path, injection_task_class_name), injection_task_class_name


class SlackTaskVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        metadata: UserTaskMetadata,
        user_task_class_name: str | None,
    ) -> None:
        self.path = path
        self.sensitive_fields = metadata.fields
        self.sensitive_literal_values = metadata.literal_values
        self.user_task_class_name = user_task_class_name
        self.violations: list[Violation] = []
        self.function_depth = 0
        self.imported_names: set[str] = set()
        self.user_task_class_assignment_count = 0
        self.dynamic_config_aliases: dict[str, str] = {}

    def add_violation(self, node: ast.AST, rule: str, detail: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0) + 1,
                rule=rule,
                detail=detail,
            )
        )

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    self.imported_names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    self.imported_names.add(alias.asname or alias.name)
        for stmt in node.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                self._check_top_level_assignment(stmt)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_parameter_usage(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_parameter_usage(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_function_scope_assignment(node)
        self._check_dynamic_method_mutation(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_function_scope_assignment(node)
        self._check_dynamic_method_mutation(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_function_scope_assignment(node)
        self._check_dynamic_method_mutation(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.function_depth > 0:
            root_name = get_forbidden_function_scope_root_name(node)
            if root_name is not None:
                self.add_violation(
                    node,
                    "Forbidden function-scope config member access",
                    f"Function scope cannot access `{ast.unparse(node)}` derived from `{root_name}`",
                )
        if is_forbidden_injection_target_reference(node):
            self.add_violation(
                node,
                "Forbidden InjectionTask target reference",
                f"Detected forbidden target reference `{ast.unparse(node)}`",
            )
        if node.attr in self.sensitive_fields:
            text = ast.unparse(node)
            self.add_violation(
                node,
                "Forbidden UserTask class attribute reference",
                f"Detected attribute access `{text}`",
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self.function_depth > 0:
            lookup_key = get_dynamic_namespace_lookup_key(node)
            if lookup_key is not None:
                self.add_violation(
                    node,
                    "Forbidden dynamic config lookup",
                    f"Function scope cannot access `{lookup_key}` via dynamic namespace lookup `{ast.unparse(node)}`",
                )
            root_name = get_forbidden_function_scope_root_name(node)
            if root_name is not None:
                self.add_violation(
                    node,
                    "Forbidden function-scope config member access",
                    f"Function scope cannot access `{ast.unparse(node)}` derived from `{root_name}`",
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.sensitive_fields:
            self.add_violation(
                node,
                "Forbidden UserTask class attribute reference",
                f"Detected name reference `{node.id}`",
            )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value in self.sensitive_literal_values:
            self.add_violation(
                node,
                "Forbidden hardcoded UserTask value",
                f"Literal `{node.value!r}` matches a class attribute value in the corresponding UserTask",
            )
            self.generic_visit(node)
            return

        self.generic_visit(node)

    def _check_top_level_assignment(
        self, node: ast.Assign | ast.AnnAssign | ast.AugAssign
    ) -> None:
        names: list[str] = []
        raw_targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(extract_assigned_names(target))
                raw_targets.extend(extract_assignment_targets(target))
        elif isinstance(node, ast.AnnAssign):
            names.extend(extract_assigned_names(node.target))
            raw_targets.extend(extract_assignment_targets(node.target))
        else:
            names.extend(extract_assigned_names(node.target))
            raw_targets.extend(extract_assignment_targets(node.target))

        for target in raw_targets:
            if isinstance(target, ast.Attribute):
                self.add_violation(
                    target,
                    "Disallowed top-level attribute assignment",
                    f"Top-level attribute assignment `{ast.unparse(target)}` is not allowed",
                )
            elif isinstance(target, ast.Subscript):
                self.add_violation(
                    target,
                    "Disallowed top-level subscript assignment",
                    f"Top-level indexed assignment `{ast.unparse(target)}` is not allowed",
                )

        for name in names:
            if name not in ALLOWED_TOP_LEVEL_ASSIGNMENTS:
                self.add_violation(
                    node,
                    "Disallowed top-level assignment",
                    f"Top-level variable `{name}` is not in the allowlist",
                )

        if "USER_TASK_CLASS" not in names:
            return

        self.user_task_class_assignment_count += 1
        if self.user_task_class_assignment_count > 1:
            self.add_violation(
                node,
                "Repeated USER_TASK_CLASS assignment",
                "USER_TASK_CLASS can only be assigned once at module scope",
            )

        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Name):
            self.add_violation(
                node,
                "Invalid USER_TASK_CLASS assignment",
                "USER_TASK_CLASS must be assigned directly to an imported class name",
            )
            return

        if value.id not in self.imported_names:
            self.add_violation(
                value,
                "Tampered USER_TASK_CLASS binding",
                f"USER_TASK_CLASS cannot be rebound to local name `{value.id}`; it must reference an imported class",
            )

    def _check_function_parameter_usage(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        body_stmts = node.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]

        used_names: set[str] = set()
        for stmt in body_stmts:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    used_names.add(child.id)

        params: list[ast.arg] = []
        params.extend(node.args.posonlyargs)
        params.extend(node.args.args)
        params.extend(node.args.kwonlyargs)
        if node.args.vararg is not None:
            params.append(node.args.vararg)
        if node.args.kwarg is not None:
            params.append(node.args.kwarg)

        for param in params:
            if param.arg not in used_names:
                self.add_violation(
                    param,
                    "Unused function parameter",
                    f"Parameter `{param.arg}` in function `{node.name}` is not used in the function body",
                )

    def _check_function_scope_assignment(
        self, node: ast.Assign | ast.AnnAssign | ast.AugAssign
    ) -> None:
        if self.function_depth == 0:
            return

        names: list[str] = []
        raw_targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(extract_assigned_names(target))
                raw_targets.extend(extract_assignment_targets(target))
        elif isinstance(node, ast.AnnAssign):
            names.extend(extract_assigned_names(node.target))
            raw_targets.extend(extract_assignment_targets(node.target))
        else:
            names.extend(extract_assigned_names(node.target))
            raw_targets.extend(extract_assignment_targets(node.target))

        for name in names:
            if name in FORBIDDEN_FUNCTION_SCOPE_ASSIGNMENTS:
                self.add_violation(
                    node,
                    "Forbidden function-scope config assignment",
                    f"Function scope cannot assign to `{name}`",
                )

        if isinstance(node, ast.AugAssign):
            return

        value = node.value
        lookup_key = get_dynamic_namespace_lookup_key(value)
        if lookup_key is not None:
            for name in names:
                self.dynamic_config_aliases[name] = lookup_key

    def _check_dynamic_method_mutation(
        self, node: ast.Assign | ast.AnnAssign | ast.AugAssign
    ) -> None:
        if self.function_depth == 0:
            return

        raw_targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                raw_targets.extend(extract_assignment_targets(target))
        elif isinstance(node, ast.AnnAssign):
            raw_targets.extend(extract_assignment_targets(node.target))
        else:
            raw_targets.extend(extract_assignment_targets(node.target))

        for target in raw_targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr not in FORBIDDEN_DYNAMIC_METHOD_MUTATIONS:
                continue
            owner_name = self._resolve_dynamic_method_owner_name(target.value)
            if owner_name is None:
                continue
            self.add_violation(
                target,
                "Forbidden dynamic trace-method mutation",
                f"Function scope cannot assign `{target.attr}` on `{owner_name}` dynamically",
            )

    def _resolve_dynamic_method_owner_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.dynamic_config_aliases:
                return self.dynamic_config_aliases[node.id]
            if node.id in FORBIDDEN_DYNAMIC_CONFIG_LOOKUPS:
                return node.id
            return None

        return get_dynamic_namespace_lookup_key(node)


class BankingUserTaskVisitor(SlackTaskVisitor):
    def __init__(
        self,
        path: Path,
        metadata: UserTaskMetadata,
        user_task_class_name: str | None,
        user_task_ground_truth_metadata: UserTaskGroundTruthMetadata,
    ) -> None:
        super().__init__(path, metadata, user_task_class_name)
        self.function_depth = 0
        self.docstring_constant_ids: set[int] = set()
        self.forbidden_ground_truth_values = set(
            user_task_ground_truth_metadata.ground_truth_string_values
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_parameter_usage(node)
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_parameter_usage(node)
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_Constant(self, node: ast.Constant) -> None:
        super().visit_Constant(node)

        if (
            self.function_depth == 0
            or not isinstance(node.value, str)
            or id(node) in self.docstring_constant_ids
        ):
            return

        if node.value in self.forbidden_ground_truth_values:
            self.add_violation(
                node,
                "Forbidden UserTask ground_truth value",
                f"String literal `{node.value!r}` matches a value used by the corresponding UserTask ground_truth",
            )


class SlackInjectionTaskVisitor(SlackTaskVisitor):
    def __init__(
        self,
        path: Path,
        metadata: UserTaskMetadata,
        user_task_class_name: str | None,
        injection_metadata: InjectionTaskMetadata,
        injection_task_class_name: str | None,
    ) -> None:
        super().__init__(path, metadata, user_task_class_name)
        self.injection_metadata = injection_metadata
        self.injection_task_class_name = injection_task_class_name
        self.function_depth = 0
        self.docstring_constant_ids: set[int] = set()
        self.forbidden_ground_truth_values = set(injection_metadata.ground_truth_string_values)
        if injection_metadata.target_value is not None:
            self.forbidden_ground_truth_values.discard(injection_metadata.target_value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_parameter_usage(node)
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_parameter_usage(node)
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            self.docstring_constant_ids.add(id(node.body[0].value))
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_Constant(self, node: ast.Constant) -> None:
        super().visit_Constant(node)

        if (
            self.function_depth == 0
            or not isinstance(node.value, str)
            or id(node) in self.docstring_constant_ids
        ):
            return

        if node.value in self.forbidden_ground_truth_values:
            self.add_violation(
                node,
                "Forbidden InjectionTask ground_truth value",
                f"String literal `{node.value!r}` matches a non-target value used by the corresponding InjectionTask ground_truth",
            )


def collect_task_files(target_dir: Path) -> list[Path]:
    return sorted(target_dir.glob("user_task_*.py"))


def collect_injection_files(target_dir: Path) -> list[Path]:
    return sorted(target_dir.glob("injection_task_*.py"))


def parse_python_file(path: Path) -> tuple[str, ast.Module] | Violation:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = path.read_text(encoding="utf-8-sig")

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return Violation(
            path=path,
            line=exc.lineno or 1,
            column=exc.offset or 1,
            rule="Python syntax error",
            detail=exc.msg,
        )

    return source, tree


def check_task_file(path: Path) -> list[Violation]:
    parsed = parse_python_file(path)
    if isinstance(parsed, Violation):
        return [parsed]
    _, tree = parsed
    metadata, user_task_class_name = load_sensitive_user_task_metadata(tree)
    user_task_ground_truth_metadata, _ = load_user_task_ground_truth_metadata(tree)
    visitor = BankingUserTaskVisitor(
        path,
        metadata,
        user_task_class_name,
        user_task_ground_truth_metadata,
    )
    visitor.visit(tree)
    return visitor.violations


def check_injection_file(path: Path) -> list[Violation]:
    parsed = parse_python_file(path)
    if isinstance(parsed, Violation):
        return [parsed]
    _, tree = parsed
    user_metadata, user_task_class_name = load_sensitive_user_task_metadata(tree)
    injection_metadata, injection_task_class_name = load_injection_task_metadata(tree)
    visitor = SlackInjectionTaskVisitor(
        path,
        user_metadata,
        user_task_class_name,
        injection_metadata,
        injection_task_class_name,
    )
    visitor.visit(tree)
    return visitor.violations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether user_task_*.py and injection_task_*.py files satisfy the validation rules."
    )
    parser.add_argument(
        "--target-dir",
        "--TARGET_DIR",
        dest="target_dir",
        default=str(DEFAULT_TARGET_DIR),
        help=(
            "Directory to scan. Default: "
            f"{DEFAULT_TARGET_DIR}"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_dir = Path(args.target_dir).expanduser().resolve()

    if not target_dir.exists():
        print(f"Target directory does not exist: {target_dir}", file=sys.stderr)
        return 2

    task_files = collect_task_files(target_dir)
    injection_files = collect_injection_files(target_dir)
    total_files = len(task_files) + len(injection_files)
    if total_files == 0:
        print(
            f"No target files found: {target_dir}/user_task_*.py or {target_dir}/injection_task_*.py"
        )
        return 0

    all_violations: list[Violation] = []
    for path in task_files:
        all_violations.extend(check_task_file(path))
    for path in injection_files:
        all_violations.extend(check_injection_file(path))

    if not all_violations:
        print("Check complete: all user_task_*.py and injection_task_*.py files satisfy the rules.")
        return 0

    current_path: Path | None = None
    for violation in all_violations:
        if violation.path != current_path:
            current_path = violation.path
            print(f"\n[{current_path}]")
        print(
            f"  L{violation.line}:C{violation.column} | {violation.rule} | {violation.detail}"
        )

    print(
        f"\nCheck complete: scanned {total_files} files and found {len(all_violations)} violations."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
