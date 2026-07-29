from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / ".github" / "rulesets" / "v1.0.0-tag-protection.json"


def _text() -> str:
    return RULESET.read_text(encoding="utf-8")


def _ruleset() -> dict:
    document = json.loads(_text())
    assert isinstance(document, dict)
    return document


def test_ruleset_path_is_the_only_v1_protection_source() -> None:
    rulesets_dir = ROOT / ".github" / "rulesets"
    entries = [p.name for p in rulesets_dir.iterdir() if p.is_file()]
    assert entries == ["v1.0.0-tag-protection.json"]


def test_ruleset_targets_only_the_v1_tag_in_repository_scope() -> None:
    ruleset = _ruleset()

    assert ruleset["name"] == "v1.0.0-tag-protection"
    assert ruleset["target"] == "tag"
    assert ruleset["source_type"] == "Repository"
    assert ruleset["enforcement"] == "active"


def test_ref_name_pattern_is_exact_and_excludes_nothing() -> None:
    conditions = _ruleset()["conditions"]
    ref_name = conditions["ref_name"]

    assert ref_name["include"] == ["refs/tags/v1.0.0"]
    assert ref_name["exclude"] == []


def test_all_four_tag_mutations_are_blocked() -> None:
    rules = _ruleset()["rules"]

    assert [rule["type"] for rule in rules] == [
        "creation",
        "deletion",
        "non_fast_forward",
        "update",
    ]
    for rule in rules:
        assert set(rule.keys()) == {"type"}


def test_no_bypass_actors_or_admin_enforcement_override_exists() -> None:
    ruleset_text = _text()
    for forbidden in (
        "bypass_actors",
        "bypassActorIds",
        "allow_admin_enforcement",
        "administration",
    ):
        assert forbidden not in ruleset_text


def test_top_level_fields_are_exactly_the_known_allowlist() -> None:
    ruleset = _ruleset()
    allowed = {
        "name",
        "target",
        "source_type",
        "enforcement",
        "conditions",
        "rules",
    }
    assert set(ruleset.keys()) == allowed


def test_ruleset_json_is_canonical_two_space_indented_utf8() -> None:
    text = _text()
    assert text.startswith("{\n  ")
    assert text.endswith("}\n")
    assert "\r" not in text
    assert "  " in text
