from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConditionSyntaxError(ValueError):
    """The author supplied an unsupported condition expression."""


class ConditionEvaluationError(ValueError):
    """A compiled condition could not be evaluated against runtime state."""


class ConditionProgram(BaseModel):
    model_config = ConfigDict(frozen=True)

    expression: str
    paths: tuple[str, ...]

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        tree = _parse(self.expression)
        try:
            return bool(_evaluate(tree.body, context))
        except ConditionEvaluationError:
            raise
        except Exception as exc:
            raise ConditionEvaluationError(str(exc)) from exc


_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "True": True,
    "False": False,
    "None": None,
}

_COMPARE_OPS = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


def _parse(expression: str) -> ast.Expression:
    if not expression or not expression.strip():
        raise ConditionSyntaxError("condition must not be empty")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ConditionSyntaxError(str(exc)) from exc
    if not isinstance(tree, ast.Expression):
        raise ConditionSyntaxError("condition must be an expression")
    return tree


def _path(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id not in _LITERALS:
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _validate(node: ast.AST, paths: set[str]) -> None:
    if isinstance(node, ast.Expression):
        _validate(node.body, paths)
        return
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate(value, paths)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _validate(node.operand, paths)
        return
    if isinstance(node, ast.Compare) and all(isinstance(op, _COMPARE_OPS) for op in node.ops):
        _validate(node.left, paths)
        for comparator in node.comparators:
            _validate(comparator, paths)
        return
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            _validate(item, paths)
        return
    if isinstance(node, ast.Name) and node.id in _LITERALS:
        return
    dotted = _path(node)
    if dotted is not None:
        paths.add(dotted)
        return
    raise ConditionSyntaxError(
        f"unsupported condition syntax: {ast.dump(node, include_attributes=False)}"
    )


def _resolve(context: Mapping[str, Any], dotted: str) -> Any:
    value: Any = context
    consumed: list[str] = []
    for part in dotted.split("."):
        consumed.append(part)
        if isinstance(value, Mapping) and part in value:
            value = value[part]
            continue
        raise ConditionEvaluationError(
            f"condition path not found: {'.'.join(consumed)} (full path: {dotted})"
        )
    return value


def _compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(operator, ast.Lt):
        return left < right
    if isinstance(operator, ast.LtE):
        return left <= right
    if isinstance(operator, ast.Gt):
        return left > right
    if isinstance(operator, ast.GtE):
        return left >= right
    if isinstance(operator, ast.In):
        return left in right
    if isinstance(operator, ast.NotIn):
        return left not in right
    raise ConditionEvaluationError(f"unsupported comparison: {type(operator).__name__}")


def _evaluate(node: ast.AST, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in _LITERALS:
        return _LITERALS[node.id]
    if isinstance(node, (ast.Name, ast.Attribute)):
        dotted = _path(node)
        if dotted is None:
            raise ConditionEvaluationError("invalid condition path")
        return _resolve(context, dotted)
    if isinstance(node, ast.List):
        return [_evaluate(item, context) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item, context) for item in node.elts)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(bool(_evaluate(item, context)) for item in node.values)
        return any(bool(_evaluate(item, context)) for item in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_evaluate(node.operand, context))
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, context)
        for operator, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, context)
            if not _compare(operator, left, right):
                return False
            left = right
        return True
    raise ConditionEvaluationError(f"unsupported node: {type(node).__name__}")


def compile_condition(expression: str) -> ConditionProgram:
    tree = _parse(expression)
    paths: set[str] = set()
    _validate(tree, paths)
    return ConditionProgram(expression=expression.strip(), paths=tuple(sorted(paths)))
