"""
queries/premios_quemados.py
Lectura (solo lectura) del registro manual de premios quemados por día y país,
usado únicamente por reporte_diario_premios.py. Nunca escribe el archivo.
"""
import json
from datetime import date
from pathlib import Path

PAISES = ("Guatemala", "El Salvador", "Honduras")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "premios_quemados.json"


class PremiosQuemadosError(RuntimeError):
    """El registro manual no permite calcular un acumulado confiable."""


def load_premios_quemados(corte: date, path: Path = DEFAULT_PATH) -> tuple[dict[str, int], list[str]]:
    """Devuelve (acumulado por país hasta `corte` inclusive, advertencias)."""
    path = Path(path)
    if not path.is_file():
        raise PremiosQuemadosError(f"No existe el archivo de premios quemados: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PremiosQuemadosError(f"Archivo de premios quemados ilegible o corrupto ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise PremiosQuemadosError("premios_quemados.json debe ser un objeto {fecha: {país: cantidad}}.")

    parsed: dict[date, dict[str, int]] = {}
    for key, row in data.items():
        try:
            day = date.fromisoformat(key)
        except (TypeError, ValueError) as exc:
            raise PremiosQuemadosError(f"Fecha inválida '{key}'; se esperaba YYYY-MM-DD.") from exc
        if day.isoformat() != key:
            raise PremiosQuemadosError(f"Fecha inválida '{key}'; se esperaba YYYY-MM-DD.")
        if not isinstance(row, dict):
            raise PremiosQuemadosError(f"{key}: se esperaba un objeto con los tres países.")
        if set(row) != set(PAISES):
            raise PremiosQuemadosError(
                f"{key}: países esperados {list(PAISES)}, encontrados {sorted(row)}.")
        for pais, value in row.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PremiosQuemadosError(
                    f"{key} / {pais}: valor inválido {value!r}; se esperaba un entero no negativo.")
        parsed[day] = row

    history = {d: r for d, r in parsed.items() if d <= corte}
    if not history:
        raise PremiosQuemadosError(
            f"No hay registros de premios quemados hasta {corte.isoformat()}.")

    warnings = []
    if corte not in history:
        last = max(history).isoformat()
        warnings.append(
            f"No se registraron premios quemados de {corte.strftime('%d/%m/%Y')}; "
            f"el acumulado incluye hasta {last}.")
    totals = {p: sum(r[p] for r in history.values()) for p in PAISES}
    return totals, warnings
