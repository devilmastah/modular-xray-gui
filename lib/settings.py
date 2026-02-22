#!/usr/bin/env python3
"""
Persist and load GUI settings to/from a JSON file.
Settings are saved when they change (call save_settings from callbacks).
Capture profiles are named copies of the full settings dict, stored in profiles/.

Module-specific defaults are collected from modules via registry.collect_module_defaults().
Only core app defaults are defined here.
"""

import json
import pathlib
import re

# Settings file in app directory (parent of lib/)
SETTINGS_DIR = pathlib.Path(__file__).resolve().parent.parent
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
PROFILES_DIR = SETTINGS_DIR / "profiles"

# Core app defaults only (module defaults are collected from modules at runtime)
CORE_DEFAULTS = {
    "acq_mode": "Dual Shot",
    "integ_time": "1 s",
    "integ_n": 1,
    "win_min": 0.0,
    "win_max": 4095.0,
    "hist_eq": False,
    "disp_scale": 1,  # 1=full res, 2=half, 4=quarter (display only; applies on next startup)
    "current_profile": "",  # last loaded or saved profile name (for Settings UI)
}

# DEFAULTS will be built at runtime by combining CORE_DEFAULTS with module defaults
# This is set by get_all_defaults() on first call
_DEFAULTS_CACHE = None


def get_all_defaults(modules=None) -> dict:
    """
    Return combined defaults: core app defaults + module defaults.
    Modules are discovered if not provided. Cached after first call.
    """
    global _DEFAULTS_CACHE
    if _DEFAULTS_CACHE is not None:
        return _DEFAULTS_CACHE
    
    if modules is None:
        # Lazy import to avoid circular dependency
        from modules.registry import discover_modules, collect_module_defaults
        modules = discover_modules()
    
    defaults = dict(CORE_DEFAULTS)
    # Add module defaults (including load_<name>_module flags)
    from modules.registry import collect_module_defaults
    module_defaults = collect_module_defaults(modules)
    defaults.update(module_defaults)
    _DEFAULTS_CACHE = defaults
    return defaults


def load_settings(extra_keys=None) -> dict:
    """Load settings from disk. Returns dict with defaults plus any extra_keys from file."""
    defaults = get_all_defaults()
    out = dict(defaults)
    if not SETTINGS_FILE.exists():
        return out
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in defaults:
            if k in data:
                out[k] = data[k]
        if extra_keys:
            for k in extra_keys:
                if k in data:
                    out[k] = data[k]
    except Exception:
        pass
    return out


def save_settings(settings_dict: dict, extra_keys=None) -> None:
    """Write settings to disk. Merges with existing file so we never drop keys."""
    defaults = get_all_defaults()
    allowed = set(defaults)
    if extra_keys:
        allowed |= set(extra_keys)
    try:
        existing = {}
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        for k in allowed:
            if k in settings_dict:
                existing[k] = settings_dict[k]
        to_write = {k: existing[k] for k in allowed if k in existing}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(to_write, f, indent=2)
    except Exception:
        pass


def _profile_filename(name: str) -> pathlib.Path:
    """Sanitize profile name for use as filename (alphanumeric, spaces → underscores)."""
    safe = re.sub(r"[^\w\s-]", "", name)
    safe = re.sub(r"[-\s]+", "_", safe).strip("_") or "profile"
    return PROFILES_DIR / f"{safe}.json"


def list_profiles():
    """Return list of profile names (filename stem, no .json). Sorted, no duplicates."""
    if not PROFILES_DIR.exists():
        return []
    names = []
    seen = set()
    for p in sorted(PROFILES_DIR.glob("*.json")):
        stem = p.stem
        if stem not in seen:
            seen.add(stem)
            names.append(stem)
    return names


def save_profile(profile_name: str, settings_dict: dict, extra_keys=None) -> None:
    """Save a copy of the given settings dict as a named profile. Creates profiles dir if needed."""
    defaults = get_all_defaults()
    allowed = set(defaults)
    if extra_keys:
        allowed |= set(extra_keys)
    to_write = {k: settings_dict[k] for k in allowed if k in settings_dict}
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _profile_filename(profile_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=2)


def load_profile(profile_name: str, extra_keys=None) -> dict:
    """Load a profile by name (stem). Returns dict with defaults plus profile keys. Raises if not found."""
    defaults = get_all_defaults()
    path = PROFILES_DIR / f"{profile_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {profile_name}")
    out = dict(defaults)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k in defaults:
        if k in data:
            out[k] = data[k]
    if extra_keys:
        for k in extra_keys:
            if k in data:
                out[k] = data[k]
    return out


def apply_profile(profile_name: str, extra_keys=None) -> None:
    """Overwrite settings.json with the given profile so next startup uses it."""
    data = load_profile(profile_name, extra_keys=extra_keys)
    defaults = get_all_defaults()
    allowed = set(defaults)
    if extra_keys:
        allowed |= set(extra_keys)
    to_write = {k: data[k] for k in allowed if k in data}
    to_write["current_profile"] = profile_name
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=2)


def set_current_profile(profile_name: str) -> None:
    """Update current_profile in settings.json (e.g. after saving a profile)."""
    try:
        existing = {}
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing["current_profile"] = profile_name
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass
