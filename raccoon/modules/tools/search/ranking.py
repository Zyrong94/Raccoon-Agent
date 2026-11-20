"""名称匹配与 BM25 排序；不依赖模型、网络或索引服务。"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from raccoon.runtime.model_types import ToolDefinition

TOOL_SEARCH_LIMIT = 8
_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[^\W_\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ToolSearchEntry:
    server: str
    tool: str
    definition: ToolDefinition


def _tokens(text: str) -> list[str]:
    tokens = []
    for word in _TOKEN.findall(text.casefold()):
        if "\u4e00" <= word[0] <= "\u9fff" and len(word) > 1:
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


def _schema_text(schema: object) -> list[str]:
    if not isinstance(schema, dict):
        return []
    text = []
    if isinstance(schema.get("description"), str):
        text.append(schema["description"])
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for name, child in value.items():
                text.append(name)
                text.extend(_schema_text(child))
        elif isinstance(value, dict):
            text.extend(_schema_text(value))
        elif isinstance(value, list):
            for child in value:
                text.extend(_schema_text(child))
    return text


def search_tool_entries(entries: tuple[ToolSearchEntry, ...], query: str) -> tuple[ToolSearchEntry, ...]:
    terms = set(_tokens(query))
    if not entries or not terms:
        return ()
    documents = []
    for entry in entries:
        text = " ".join([entry.definition.description or "", *_schema_text(entry.definition.parameters)])
        documents.append((set(_tokens(entry.definition.name)), set(_tokens(entry.server)), Counter(_tokens(text))))
    average_length = sum(sum(counts.values()) for _, _, counts in documents) / len(documents) or 1
    inverse_frequency = {}
    for term in terms:
        frequency = sum(term in names or term in server or term in counts for names, server, counts in documents)
        inverse_frequency[term] = math.log(1 + (len(documents) - frequency + 0.5) / (frequency + 0.5))
    ranked = []
    for entry, (names, server, counts) in zip(entries, documents, strict=True):
        normalization = 1.2 * (1 - 0.75 + 0.75 * sum(counts.values()) / average_length)
        score = 0.0
        coverage = 0
        for term in terms:
            frequency = counts[term]
            weight = frequency * 2.2 / (frequency + normalization)
            if term in names:
                weight += 3
            if term in server:
                weight += 0.5
            if weight:
                coverage += 1
                score += inverse_frequency[term] * weight
        if score:
            exact = query.strip().casefold() in {entry.tool.casefold(), entry.definition.name.casefold()}
            ranked.append((not exact, -coverage, -score, entry.definition.name, entry))
    ranked.sort(key=lambda row: row[:4])
    return tuple(row[4] for row in ranked[:TOOL_SEARCH_LIMIT])
