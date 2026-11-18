"""Le configuracoes aprovadas sem fazer rede durante a decupagem."""
from __future__ import annotations

from cloud_learning import get_cached_configuration


def regras_aprovadas(project_type: str = "videoaula") -> list[str]:
    profiles = {
        "videoaula": "videoaula_editorial_v1",
        "videocast": "videocast_editorial_camera_v1",
        "videocast_graduacao": "videocast_graduacao_editorial_v1",
        "videocast_mba": "videocast_mba_editorial_v1",
    }
    config = get_cached_configuration(profiles.get(project_type, "videoaula_editorial_v1"))
    return list(config.get("rules", [])) if config else []
