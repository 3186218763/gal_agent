from pathlib import Path
import yaml
from src.domain.setting_pack import SettingPack


def load_setting_pack(scripts_dir: Path | str, pack_id: str) -> SettingPack:
    base = Path(scripts_dir) / pack_id
    path = base / "setting_pack.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Setting pack not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid setting pack YAML: {path}")
    return SettingPack.model_validate(data)
