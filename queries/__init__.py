from .trivias import get_rendimiento, get_detalle, get_resultado
from .premios import (
    get_cupones,
    get_reclamos,
    get_resumen_campana,
    get_premios_hoy,
    get_historico,
    get_config_gp,
)

__all__ = [
    "get_rendimiento", "get_detalle", "get_resultado",
    "get_cupones", "get_reclamos", "get_resumen_campana",
    "get_premios_hoy", "get_historico", "get_config_gp",
]
