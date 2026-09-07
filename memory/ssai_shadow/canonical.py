"""Deterministic JSON serialization for the SSAI SHADOW prototype.

The encoder implements the RFC 8785 / JCS rules for the I-JSON data model used
by SSAI manifests: JSON objects and arrays, strings, booleans, null, safe JSON
integers, and finite IEEE-754 binary64 values.

Callers that start with JSON text should use :func:`parse_json_strict` (or
:func:`canonicalize_json`) so duplicate member names are rejected before a
Python ``dict`` can discard them.
"""

from __future__ import annotations

import json
import math
from typing import Any


MAX_SAFE_INTEGER = (1 << 53) - 1
MIN_SAFE_INTEGER = -MAX_SAFE_INTEGER


class CanonicalizationError(ValueError):
    """The supplied value cannot be represented as canonical I-JSON."""


def _reject_constant(token: str) -> None:
    raise CanonicalizationError(f"non-finite JSON number is forbidden: {token}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalizationError(f"duplicate JSON object member: {key!r}")
        result[key] = value
    return result


def parse_json_strict(raw: str | bytes | bytearray) -> Any:
    """Parse JSON while rejecting duplicate keys and non-I-JSON values."""

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except CanonicalizationError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CanonicalizationError("invalid JSON input") from exc

    # Validation is deliberately shared with canonicalize().  This catches
    # overflowed floats (for example 1e400), unsafe integers and lone UTF-16
    # surrogates accepted by Python's permissive JSON decoder.
    canonicalize(value)
    return value


def canonicalize_json(raw: str | bytes | bytearray) -> bytes:
    """Strictly parse and canonicalize JSON text."""

    return canonicalize(parse_json_strict(raw))


def _validate_string(value: str) -> None:
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CanonicalizationError("lone UTF-16 surrogate is forbidden by I-JSON")


def _encode_string(value: str) -> str:
    _validate_string(value)
    # Python's JSON encoder uses the JCS-required short escapes, emits the
    # remaining controls as lower-case \u00xx, and leaves other Unicode scalar
    # values unescaped when ensure_ascii=False.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utf16_sort_key(value: str) -> bytes:
    _validate_string(value)
    # RFC 8785 sorts object member names by their UTF-16 code units, not by
    # Unicode scalar value or UTF-8 bytes.
    return value.encode("utf-16-be")


def _expand_exponent(digits: str, exponent: int) -> str:
    decimal_position = 1 + exponent
    if decimal_position <= 0:
        return "0." + ("0" * -decimal_position) + digits
    if decimal_position >= len(digits):
        return digits + ("0" * (decimal_position - len(digits)))
    return digits[:decimal_position] + "." + digits[decimal_position:]


def _encode_float(value: float) -> str:
    if not math.isfinite(value):
        raise CanonicalizationError("NaN and Infinity are forbidden by I-JSON")
    if value == 0.0:
        # ECMAScript JSON serialization maps both +0 and -0 to "0".
        return "0"

    negative = value < 0
    magnitude = -value if negative else value
    rendered = repr(magnitude).lower()

    if "e" not in rendered:
        # Python distinguishes an integral float with a trailing ``.0``;
        # ECMAScript's Number serialization does not.
        number = rendered[:-2] if rendered.endswith(".0") else rendered
    else:
        mantissa, raw_exponent = rendered.split("e", 1)
        exponent = int(raw_exponent)
        digits = mantissa.replace(".", "")

        # ECMAScript Number::toString uses plain notation for this interval.
        # Python's repr() and ECMAScript both first choose a shortest decimal
        # that round-trips to the same binary64 value; this step reconciles the
        # notation thresholds and exponent formatting.
        if 1e-6 <= magnitude < 1e21:
            number = _expand_exponent(digits, exponent)
        else:
            fractional = digits[1:]
            normalized_mantissa = digits[0]
            if fractional:
                normalized_mantissa += "." + fractional
            exponent_sign = "+" if exponent >= 0 else "-"
            number = f"{normalized_mantissa}e{exponent_sign}{abs(exponent)}"

    return ("-" if negative else "") + number


def _encode(value: Any) -> str:
    value_type = type(value)

    if value is None:
        return "null"
    if value_type is bool:
        return "true" if value else "false"
    if value_type is str:
        return _encode_string(value)
    if value_type is int:
        if not MIN_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalizationError(
                f"integer outside the I-JSON safe range: {value}"
            )
        return str(value)
    if value_type is float:
        return _encode_float(value)
    if value_type is list:
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if value_type is dict:
        for key in value:
            if type(key) is not str:
                raise CanonicalizationError("JSON object member names must be strings")
        members = (
            _encode_string(key) + ":" + _encode(value[key])
            for key in sorted(value, key=_utf16_sort_key)
        )
        return "{" + ",".join(members) + "}"

    raise CanonicalizationError(f"unsupported JSON value type: {value_type.__name__}")


def canonicalize(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes, or fail closed.

    The function only accepts actual JSON-domain Python types.  Types with
    surprising serialization behavior (tuples, Decimal, custom mappings,
    integer subclasses, and so on) are intentionally rejected.
    """

    try:
        return _encode(value).encode("utf-8")
    except CanonicalizationError:
        raise
    except (RecursionError, UnicodeError, OverflowError, TypeError, ValueError) as exc:
        raise CanonicalizationError("unable to canonicalize value") from exc
