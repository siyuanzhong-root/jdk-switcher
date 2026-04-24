import ctypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import winreg


SYSTEM_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
USER_ENV_KEY = r"Environment"
JAVA_BIN_ALIAS = r"%JAVA_HOME%\bin"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _broadcast_env_change() -> None:
    """Notify Windows that environment variables have changed."""
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = ctypes.c_long()
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        "Environment",
        SMTO_ABORTIFHUNG,
        5000,
        ctypes.byref(result),
    )


def _normalize_dir(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.rstrip("\\/"))) if path else ""


def _get_env_value(hive, key_path: str, name: str) -> str:
    try:
        with winreg.OpenKey(hive, key_path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return ""


def _set_env_value(hive, key_path: str, name: str, value: str) -> None:
    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)


def _read_registry_env(hive, key_path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with winreg.OpenKey(hive, key_path) as key:
            index = 0
            while True:
                name, value, _ = winreg.EnumValue(key, index)
                values[name] = str(value)
                index += 1
    except FileNotFoundError:
        return values
    except OSError:
        return values
    return values


def _lookup_env(env: dict[str, str], key: str) -> str:
    key_upper = key.upper()
    for env_key, env_value in env.items():
        if env_key.upper() == key_upper:
            return env_value
    return ""


def _expand_env_vars(value: str, env: dict[str, str]) -> str:
    pattern = re.compile(r"%([^%]+)%")
    previous = value
    for _ in range(5):
        expanded = pattern.sub(lambda m: _lookup_env(env, m.group(1)) or m.group(0), previous)
        if expanded == previous:
            return expanded
        previous = expanded
    return previous


def get_effective_environment() -> dict[str, str]:
    """
    Build the environment a fresh Windows process would see after merging
    system and user registry-backed variables.
    """
    env = dict(os.environ)
    system_env = _read_registry_env(winreg.HKEY_LOCAL_MACHINE, SYSTEM_ENV_KEY)
    user_env = _read_registry_env(winreg.HKEY_CURRENT_USER, USER_ENV_KEY)

    system_path = system_env.pop("Path", system_env.pop("PATH", ""))
    user_path = user_env.pop("Path", user_env.pop("PATH", ""))

    for key, value in system_env.items():
        env[key] = _expand_env_vars(value, env)
    for key, value in user_env.items():
        env[key] = _expand_env_vars(value, env)

    merged_path = system_path
    if user_path:
        merged_path = f"{merged_path};{user_path}" if merged_path else user_path

    if merged_path:
        env["Path"] = _expand_env_vars(merged_path, env)

    return env


def get_current_java_home() -> str:
    """
    Return the effective JAVA_HOME used by new Windows processes.

    On Windows, a user environment variable overrides a system variable with
    the same name, so HKCU must be checked before HKLM.
    """
    user_java_home = _get_env_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, "JAVA_HOME")
    if user_java_home:
        return user_java_home

    return _get_env_value(winreg.HKEY_LOCAL_MACHINE, SYSTEM_ENV_KEY, "JAVA_HOME")


def _is_java_bin_path(path_item: str, java_homes: list[str]) -> bool:
    path_norm = _normalize_dir(path_item)
    if not path_norm:
        return False

    alias_norm = _normalize_dir(JAVA_BIN_ALIAS)
    if path_norm == alias_norm:
        return True

    for java_home in java_homes:
        if java_home and path_norm == _normalize_dir(os.path.join(java_home, "bin")):
            return True

    return False


def _is_java_command_dir(path_item: str) -> bool:
    expanded = _expand_env_vars(path_item, get_effective_environment()).strip()
    if not expanded:
        return False

    path_obj = Path(expanded)
    if path_obj.name.lower() == "javapath":
        return True

    java_exe = path_obj / "java.exe"
    return java_exe.exists()


def _should_remove_java_path(path_item: str, java_homes: list[str]) -> bool:
    return _is_java_bin_path(path_item, java_homes) or _is_java_command_dir(path_item)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        norm = _normalize_dir(item)
        if norm in seen:
            continue
        seen.add(norm)
        result.append(item)
    return result


def _update_path(hive, key_path: str, old_java_homes: list[str], new_java_home: str) -> None:
    """Remove stale JDK bin entries and prepend %JAVA_HOME%\\bin."""
    path_val = _get_env_value(hive, key_path, "Path")
    parts = [part.strip() for part in path_val.split(";") if part.strip()]

    known_homes = [home for home in old_java_homes if home]
    known_homes.append(new_java_home)

    cleaned_parts = [part for part in parts if not _should_remove_java_path(part, known_homes)]
    updated_parts = _dedupe_preserve_order([JAVA_BIN_ALIAS, *cleaned_parts])
    _set_env_value(hive, key_path, "Path", ";".join(updated_parts))


def _update_process_env(new_java_home: str) -> None:
    """Keep the current GUI process in sync with the registry write."""
    os.environ["JAVA_HOME"] = new_java_home

    current_parts = [part.strip() for part in os.environ.get("Path", "").split(";") if part.strip()]
    java_homes = [
        get_current_java_home(),
        _get_env_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, "JAVA_HOME"),
        _get_env_value(winreg.HKEY_LOCAL_MACHINE, SYSTEM_ENV_KEY, "JAVA_HOME"),
    ]
    filtered_parts = [part for part in current_parts if not _should_remove_java_path(part, java_homes)]
    os.environ["Path"] = ";".join(_dedupe_preserve_order([str(Path(new_java_home) / "bin"), *filtered_parts]))


def diagnose_path() -> str:
    lines = []
    for label, hive, key_path in [
        ("System", winreg.HKEY_LOCAL_MACHINE, SYSTEM_ENV_KEY),
        ("User", winreg.HKEY_CURRENT_USER, USER_ENV_KEY),
    ]:
        value = _get_env_value(hive, key_path, "Path")
        hits = [part for part in value.split(";") if "java" in part.lower() or "jdk" in part.lower()]
        if hits:
            lines.append(f"[{label} PATH]")
            lines.extend(f"  {item}" for item in hits)

    return "\n".join(lines) if lines else "No java/jdk related entries were found in PATH."


def get_effective_java_runtime() -> dict[str, str]:
    """
    Resolve the Java executable and version a fresh process would use based on
    merged registry environment variables.
    """
    env = get_effective_environment()
    java_path = shutil.which("java", path=env.get("Path", ""))
    if not java_path:
        return {"java_path": "", "version": "java not found in effective PATH"}

    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        output = (result.stderr or result.stdout).strip()
        version_line = output.splitlines()[0].strip() if output else "unknown version"
        return {"java_path": java_path, "version": version_line}
    except Exception as exc:
        return {"java_path": java_path, "version": f"failed to read version: {exc}"}


def switch_jdk(new_java_home: str) -> tuple[bool, str]:
    """Switch JDK by updating registry-backed environment variables."""
    java_home_path = Path(new_java_home)
    java_exe = java_home_path / "bin" / "java.exe"
    javac_exe = java_home_path / "bin" / "javac.exe"

    if not java_home_path.exists():
        return False, f"Target path does not exist:\n{new_java_home}"
    if not java_exe.exists() or not javac_exe.exists():
        return False, f"Selected folder is not a valid JDK root:\n{new_java_home}"

    current_user_java_home = _get_env_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, "JAVA_HOME")
    current_system_java_home = _get_env_value(winreg.HKEY_LOCAL_MACHINE, SYSTEM_ENV_KEY, "JAVA_HOME")
    effective_old_java_home = get_current_java_home()

    if is_admin():
        hive = winreg.HKEY_LOCAL_MACHINE
        key_path = SYSTEM_ENV_KEY
        scope = "system"
        old_java_homes = [current_system_java_home, effective_old_java_home]
    else:
        hive = winreg.HKEY_CURRENT_USER
        key_path = USER_ENV_KEY
        scope = "user"
        old_java_homes = [current_user_java_home, effective_old_java_home, current_system_java_home]

    try:
        _set_env_value(hive, key_path, "JAVA_HOME", new_java_home)
        _update_path(hive, key_path, old_java_homes, new_java_home)
        _update_process_env(new_java_home)
        _broadcast_env_change()

        effective_java_home = get_current_java_home()
        runtime = get_effective_java_runtime()
        diag = diagnose_path()
        message = (
            f"JDK switched successfully ({scope} scope).\n"
            f"Effective JAVA_HOME:\n{effective_java_home}\n\n"
            f"Effective java.exe:\n{runtime['java_path'] or '(not found)'}\n"
            f"Effective Java version:\n{runtime['version']}\n\n"
            f"--- PATH diagnostics ---\n{diag}"
        )
        return True, message
    except PermissionError:
        return False, "Permission denied. Please run the app as administrator."
    except OSError as exc:
        return False, f"Registry update failed: {exc}"
    except Exception as exc:
        return False, f"JDK switch failed: {exc}"
