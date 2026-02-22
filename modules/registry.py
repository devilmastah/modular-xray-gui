"""
Discover modules packages and their metadata.
Modules live under modules/<type>/<name>/ (detector, machine, image_processing, workflow_automation).
Enables adding modules without editing gui.py: each module declares MODULE_INFO
and optionally get_setting_keys(); the GUI uses this to build Settings checkboxes
and load/save module-specific settings.
"""

import importlib
import pkgutil
import sys
from typing import Any

MODULES_PACKAGE = "modules"
_TYPE_SUBPACKAGES = ("detector", "machine", "image_processing", "workflow_automation")


def _discover_entries() -> list[tuple[str, str]]:
    """Return list of (name, import_path) for all leaf modules under modules/<type>/."""
    entries: list[tuple[str, str]] = []
    try:
        mod = sys.modules.get(MODULES_PACKAGE)
        if mod is None:
            mod = importlib.import_module(MODULES_PACKAGE)
        pkgpath = getattr(mod, "__path__", None)
        if pkgpath is None:
            return []
        for type_name in _TYPE_SUBPACKAGES:
            try:
                submod = importlib.import_module(f"{MODULES_PACKAGE}.{type_name}")
                subpath = getattr(submod, "__path__", None)
                if subpath is None:
                    continue
                for _importer, name, _ispkg in pkgutil.iter_modules(subpath):
                    if name.startswith("_"):
                        continue
                    import_path = f"{MODULES_PACKAGE}.{type_name}.{name}"
                    entries.append((name, import_path))
            except Exception:
                continue
        return sorted(entries, key=lambda x: x[0])
    except Exception:
        return []


def get_module_info(import_path: str) -> dict[str, Any]:
    """
    Import by import_path and return MODULE_INFO (or defaults).
    Returns dict with: display_name, description, type, default_enabled,
    camera_priority (if detector), pipeline_slot (if image_processing), setting_keys (list).
    """
    name = import_path.split(".")[-1] if "." in import_path else import_path
    defaults = {
        "display_name": name.replace("_", " ").title(),
        "description": "Applies on next startup.",
        "type": "machine",
        "default_enabled": False,
        "camera_priority": 0,
        "pipeline_slot": 0,
        "setting_keys": [],
    }
    try:
        mod = importlib.import_module(import_path)
        info = getattr(mod, "MODULE_INFO", None)
        if isinstance(info, dict):
            defaults.update(info)
        get_sk = getattr(mod, "get_setting_keys", None)
        if callable(get_sk):
            try:
                keys = get_sk()
                if isinstance(keys, (list, tuple)):
                    defaults["setting_keys"] = list(keys)
            except Exception:
                pass
    except Exception:
        pass
    return defaults


def discover_modules() -> list[dict[str, Any]]:
    """
    Return list of module info dicts for all discovered packages under modules/<type>/.
    Each dict has: name, import_path, display_name, description, type, default_enabled,
    camera_priority (if detector), setting_keys.
    """
    result = []
    for name, import_path in _discover_entries():
        info = get_module_info(import_path)
        info["name"] = name
        info["import_path"] = import_path
        result.append(info)
    return result


def all_extra_settings_keys(modules: list[dict[str, Any]]) -> set[str]:
    """Return set of all setting keys to persist: load_<name>_module plus each module's setting_keys."""
    keys = set()
    for m in modules:
        keys.add(f"load_{m['name']}_module")
        keys.update(m.get("setting_keys") or [])
    return keys


def collect_module_defaults(modules: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Collect default settings from all modules.
    Uses import_path for each module.
    """
    defaults = {}
    for m in modules:
        name = m["name"]
        import_path = m.get("import_path", f"{MODULES_PACKAGE}.{name}")
        defaults[f"load_{name}_module"] = m.get("default_enabled", False)
        try:
            mod = importlib.import_module(import_path)
            get_defaults = getattr(mod, "get_default_settings", None)
            if callable(get_defaults):
                try:
                    module_defaults = get_defaults()
                    if isinstance(module_defaults, dict):
                        defaults.update(module_defaults)
                except Exception:
                    pass
        except Exception:
            pass
    return defaults
