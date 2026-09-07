#!/usr/bin/env python3
"""Typed integer-only recovery for trusted named values."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

KEY = re.compile(r"\bK[0-9A-Za-z_]+\b")


def calculate(expression: str) -> int:
    if len(expression) > 300:
        raise ValueError("expression too long")
    tree = ast.parse(expression, mode="eval")

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if abs(node.value) > 1_000_000:
                raise ValueError("constant out of range")
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            left, right = visit(node.left), visit(node.right)
            value = left + right if isinstance(node.op, ast.Add) else left - right if isinstance(node.op, ast.Sub) else left * right
            if abs(value) > 1_000_000_000_000:
                raise ValueError("result out of range")
            return value
        raise ValueError(f"unsupported expression node: {type(node).__name__}")

    return visit(tree)


def recover(payload: dict) -> dict:
    expression = str(payload.get("expression", ""))
    trusted = payload.get("trusted_values")
    if not isinstance(trusted, dict):
        raise ValueError("trusted_values must be an object")

    def replace(match: re.Match[str]) -> str:
        name = match.group(0)
        value = trusted.get(name)
        if type(value) is not int:
            raise ValueError(f"missing or non-integer trusted value: {name}")
        return str(value)

    numeric = KEY.sub(replace, expression)
    if KEY.search(numeric):
        raise ValueError("unresolved trusted value")
    value = calculate(numeric)
    return {"ok": True, "result": value, "final": f"FINAL: {value}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        print(json.dumps(recover(payload), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
