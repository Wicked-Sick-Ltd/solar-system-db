"""Decode JSON stored in catalogue cells, with a nesting bound."""
from __future__ import annotations

import json
import re
from itertools import accumulate

# Real catalogue cells nest at most a handful of levels.
MAX_DEPTH = 32

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_NOT_BRACKET = re.compile(r"[^\[\]{}]+")
_STEP = {"[": 1, "{": 1, "]": -1, "}": -1}


def loads(text, max_depth: int = MAX_DEPTH):
    """``json.loads`` that raises ValueError for documents nested past ``max_depth``.

    The catalogue is a published artefact, so a cell may hold any valid JSON.
    The standard decoder raises RecursionError, not ValueError, on deep nesting;
    callers that treat ValueError as a bad cell must see the same exception here.
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode(json.detect_encoding(text), "surrogatepass")
    if isinstance(text, str):
        brackets = _NOT_BRACKET.sub("", _STRING.sub("", text))
        if max(accumulate(map(_STEP.__getitem__, brackets)), default=0) > max_depth:
            raise ValueError(f"JSON nested deeper than {max_depth} levels")
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise ValueError("JSON nested too deeply to decode") from exc
