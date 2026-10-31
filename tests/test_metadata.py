"""Согласованность manifest и описаний сервисов."""

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "tasklerq"

SERVICE_KEYS = {"capture", "confirm_capture", "cancel_capture", "undo_last_capture"}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def test_manifest_required_keys() -> None:
    """Manifest содержит обязательные для hassfest ключи."""
    manifest = _load_json(COMPONENT / "manifest.json")

    assert manifest["domain"] == "tasklerq"
    assert manifest["name"]
    assert manifest["version"] == "0.1.0"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["documentation"] == "https://github.com/StellaLupus/tasklerq-ha"
    assert manifest["issue_tracker"] == "https://github.com/StellaLupus/tasklerq-ha"
    assert manifest["codeowners"] == ["@StellaLupus"]
    assert manifest["requirements"] == []


def test_manifest_keys_are_sorted() -> None:
    """После domain и name ключи manifest идут по алфавиту."""
    keys = list(_load_json(COMPONENT / "manifest.json"))

    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_services_are_consistent() -> None:
    """services.yaml, strings.json и ru.json описывают одни и те же сервисы."""
    services = _load_yaml(COMPONENT / "services.yaml")
    strings = _load_json(COMPONENT / "strings.json")
    ru = _load_json(COMPONENT / "translations" / "ru.json")

    assert set(services) == SERVICE_KEYS
    assert set(strings["services"]) == SERVICE_KEYS
    assert set(ru["services"]) == SERVICE_KEYS

    for service in SERVICE_KEYS:
        fields = set(services[service].get("fields", {}))
        assert set(strings["services"][service]["fields"]) == fields
        assert set(ru["services"][service]["fields"]) == fields


def test_translations_have_same_keys() -> None:
    """strings.json и translations/ru.json совпадают по ключам."""
    strings = _load_json(COMPONENT / "strings.json")
    ru = _load_json(COMPONENT / "translations" / "ru.json")

    assert _key_tree(strings) == _key_tree(ru)
    assert set(strings["config"]["step"]["user"]["data"]) == {"url", "token"}


def _key_tree(value: Any) -> Any:
    """Превращает вложенный словарь в сортированное дерево ключей."""
    if isinstance(value, dict):
        return tuple(sorted((key, _key_tree(item)) for key, item in value.items()))
    return None
