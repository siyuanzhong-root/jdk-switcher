import os
import subprocess
from pathlib import Path


def get_java_version(java_exe: str) -> str:
    """执行 java -version 获取版本号"""
    try:
        result = subprocess.run(
            [java_exe, "-version"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stderr or result.stdout
        for line in output.splitlines():
            if "version" in line:
                return line.strip()
    except Exception:
        pass
    return "未知版本"


def scan_jdks(root: str = "D:\\", max_depth: int = 5) -> list[dict]:
    """
    递归扫描指定目录，查找所有 JDK 安装（含 bin/java.exe）。
    返回 [{"path": ..., "version": ...}, ...]
    """
    found = []
    root_path = Path(root)

    def _walk(path: Path, depth: int):
        if depth > max_depth:
            return
        try:
            java_exe = path / "bin" / "java.exe"
            javac_exe = path / "bin" / "javac.exe"
            if java_exe.exists() and javac_exe.exists():
                version = get_java_version(str(java_exe))
                found.append({"path": str(path), "version": version})
                return  # 不再深入已找到的 JDK 目录
            for child in path.iterdir():
                if child.is_dir():
                    _walk(child, depth + 1)
        except PermissionError:
            pass
        except Exception:
            pass

    _walk(root_path, 0)
    return found
