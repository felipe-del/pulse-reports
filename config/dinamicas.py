"""Carga y validacion de la configuracion JSON de dinamicas."""
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class Dynamics:
    key: str
    nombre: str
    descripcion: str
    pais: str
    zona_horaria: str
    fecha_inicio: Optional[date]
    fecha_fin: Optional[date]
    variantes: list[dict]

DEFAULT_CONFIG = Path(__file__).with_name("dinamicas.json")
_CODE = re.compile(r"^[A-Z0-9_-]+$")
_EVENT_FIELDS = ("eventos_vista", "eventos_cta")


class DynamicsConfigError(ValueError):
    """Configuracion de dinamicas invalida."""


def _configured_date(value, path: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DynamicsConfigError(f"{path} debe ser una fecha YYYY-MM-DD o null.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DynamicsConfigError(f"{path} no es una fecha YYYY-MM-DD valida.") from exc
    if parsed.isoformat() != value:
        raise DynamicsConfigError(f"{path} debe usar el formato YYYY-MM-DD.")
    return parsed


def _positive_id(value, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DynamicsConfigError(f"{path} debe ser un entero positivo.")
    return value


def validate_config(raw: object, selected: str | None = None) -> Dynamics:
    if not isinstance(raw, dict):
        raise DynamicsConfigError("La raiz del JSON debe ser un objeto.")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise DynamicsConfigError("version debe ser 1.")
    active = raw.get("dinamica_activa")
    dynamics = raw.get("dinamicas")
    if not isinstance(dynamics, dict) or not dynamics:
        raise DynamicsConfigError("dinamicas debe contener al menos una dinamica.")
    key = selected or active
    if not isinstance(key, str) or key not in dynamics:
        raise DynamicsConfigError(f"No existe la dinamica seleccionada: {key!r}.")

    definition = dynamics[key]
    if not isinstance(definition, dict):
        raise DynamicsConfigError(f"dinamicas.{key} debe ser un objeto.")
    for field in ("nombre", "descripcion", "pais", "zona_horaria"):
        if not isinstance(definition.get(field), str) or not definition[field].strip():
            raise DynamicsConfigError(f"dinamicas.{key}.{field} debe ser texto no vacio.")
    try:
        ZoneInfo(definition["zona_horaria"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DynamicsConfigError(
            f"Zona horaria desconocida: {definition['zona_horaria']!r}.") from exc

    start = _configured_date(definition.get("fecha_inicio"), f"dinamicas.{key}.fecha_inicio")
    end = _configured_date(definition.get("fecha_fin"), f"dinamicas.{key}.fecha_fin")
    if start and end and start > end:
        raise DynamicsConfigError("fecha_inicio no puede ser posterior a fecha_fin.")
    variants = definition.get("variantes")
    if not isinstance(variants, list) or not variants:
        raise DynamicsConfigError(f"dinamicas.{key}.variantes debe ser una lista no vacia.")

    normalized, codes = [], set()
    id_fields = ("historia_id", "campania_raspadita_id", "reto_id", "cupon_id")
    ids_by_type = {field: set() for field in id_fields}
    for index, variant in enumerate(variants):
        prefix = f"dinamicas.{key}.variantes[{index}]"
        if not isinstance(variant, dict):
            raise DynamicsConfigError(f"{prefix} debe ser un objeto.")
        code = variant.get("codigo")
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise DynamicsConfigError(f"{prefix}.codigo debe usar A-Z, 0-9, guion o guion bajo.")
        if code.casefold() in codes:
            raise DynamicsConfigError(f"Codigo de variante duplicado: {code}.")
        codes.add(code.casefold())
        name = variant.get("nombre")
        if not isinstance(name, str) or not name.strip():
            raise DynamicsConfigError(f"{prefix}.nombre debe ser texto no vacio.")
        item = {"codigo": code, "nombre": name.strip()}
        for field in id_fields:
            value = _positive_id(variant.get(field), f"{prefix}.{field}")
            if value in ids_by_type[field]:
                raise DynamicsConfigError(f"ID {field} duplicado entre variantes: {value}.")
            ids_by_type[field].add(value)
            item[field] = value
        for field in _EVENT_FIELDS:
            value = variant.get(field)
            if value is not None and (
                not isinstance(value, list)
                or not value
                or any(not isinstance(event, str) or not event.strip() for event in value)
            ):
                raise DynamicsConfigError(
                    f"{prefix}.{field} debe ser null o una lista no vacia de descripciones.")
            item[field] = [event.strip() for event in value] if value else None
        normalized.append(item)

    return Dynamics(
        key=key,
        nombre=definition["nombre"].strip(),
        descripcion=definition["descripcion"].strip(),
        pais=definition["pais"].strip(),
        zona_horaria=definition["zona_horaria"],
        fecha_inicio=start,
        fecha_fin=end,
        variantes=normalized,
    )


def load_dynamics(path: str | Path = DEFAULT_CONFIG,
                  selected: str | None = None) -> Dynamics:
    try:
        with Path(path).open(encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DynamicsConfigError(f"No se pudo leer la configuracion JSON: {exc}") from exc
    return validate_config(raw, selected)
