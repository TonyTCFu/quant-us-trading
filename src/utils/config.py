"""配置加载工具：JSON 文件 + 环境变量覆盖。"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _load_dotenv(path: str = ".env") -> None:
    """精简 .env 加载器，不依赖 python-dotenv。

    仅处理 KEY=VALUE 格式，忽略注释和空行。
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _find_project_root() -> Path:
    """从当前文件向上查找包含 config/default.json 的项目根目录。"""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "config" / "default.json").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载配置：先读 JSON，再用环境变量覆盖。

    优先级：环境变量 > JSON > 无（报错）
    """
    _load_dotenv()

    if config_path is None:
        root = _find_project_root()
        config_path = str(root / "config" / "default.json")

    with open(config_path, "r") as f:
        config = json.load(f)

    # 移除 _comment 等元数据 key
    config.pop("_comment", None)

    # 环境变量覆盖
    env_overrides = {
        "DATA_CACHE_DIR": ("data", "cache_dir"),
        "BACKTEST_INITIAL_CAPITAL": ("backtest", "initial_capital"),
        "RISK_MAX_POSITION_PCT": ("risk", "max_position_pct"),
    }
    for env_key, (section, key) in env_overrides.items():
        val = os.getenv(env_key)
        if val is not None:
            typ = type(config[section][key])
            config[section][key] = typ(val)

    return config
