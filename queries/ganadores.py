"""
queries/ganadores.py
Ganadores de las campañas Gane Premios de Grupo UNO (GT, SV, HN).

Fuente principal: "CustomerPrizeWins". Solo consultas SELECT parametrizadas,
ejecutadas con la conexión de solo lectura existente (utils.db.get_connection).

A diferencia de utils.db.query_df, aquí los errores se propagan: un reporte
de conciliación no debe confundir un fallo de BD con "cero ganadores".
"""
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from config.settings import DatabaseConfig
from utils.db import get_connection

logger = logging.getLogger(__name__)

DEFAULT_TZ = "America/Guatemala"  # UTC-6 todo el año, igual que HN y SV

# Catálogo ordenado: define el orden de las hojas del Excel.
CAMPAIGN_CATALOG: list[dict] = [
    {"campaign_id": 818, "pais": "Guatemala",   "codigo": "GT", "premio": "Carro 1"},
    {"campaign_id": 819, "pais": "Guatemala",   "codigo": "GT", "premio": "Carro 2"},
    {"campaign_id": 820, "pais": "Guatemala",   "codigo": "GT", "premio": "Moto"},
    {"campaign_id": 825, "pais": "El Salvador", "codigo": "SV", "premio": "Carro"},
    {"campaign_id": 826, "pais": "El Salvador", "codigo": "SV", "premio": "Moto"},
    {"campaign_id": 828, "pais": "Honduras",    "codigo": "HN", "premio": "Carro"},
    {"campaign_id": 829, "pais": "Honduras",    "codigo": "HN", "premio": "Moto"},
]


def sheet_name(c: dict) -> str:
    return f"{c['codigo']} - {c['premio']} - {c['campaign_id']}"


def _norm(txt: str) -> str:
    t = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return t.strip().lower()


@dataclass
class WinnersFilter:
    paises: list[str] = field(default_factory=list)       # nombre o código (GT/SV/HN)
    campanias: list[int] = field(default_factory=list)
    desde: Optional[date] = None                          # inclusive
    hasta: Optional[date] = None                          # inclusive
    estados: list[str] = field(default_factory=list)      # valores de status
    tz: str = DEFAULT_TZ
    usar_inicio_campania: bool = False
    inicios_campania: dict[int, datetime] = field(default_factory=dict)
    hasta_exclusivo: Optional[datetime] = None
    corte_hasta: Optional[date] = None

    def selected_catalog(self) -> list[dict]:
        paises = {_norm(p) for p in self.paises}
        cat = CAMPAIGN_CATALOG
        if paises:
            cat = [c for c in cat
                   if _norm(c["pais"]) in paises or _norm(c["codigo"]) in paises]
        if self.campanias:
            cat = [c for c in cat if c["campaign_id"] in set(self.campanias)]
        return cat

    def describe(self) -> str:
        parts = []
        if self.paises:
            parts.append("País: " + ", ".join(self.paises))
        if self.campanias:
            parts.append("Campaña: " + ", ".join(map(str, self.campanias)))
        if self.desde:
            parts.append(f"Desde: {self.desde:%d/%m/%Y}")
        if self.hasta:
            parts.append(f"Hasta: {self.hasta:%d/%m/%Y}")
        if self.corte_hasta:
            parts.append(f"Corte: {self.corte_hasta:%d/%m/%Y} (inclusive)")
        if self.estados:
            parts.append("Estado: " + ", ".join(self.estados))
        return " | ".join(parts) if parts else "Sin filtros (todos los registros)"


# ── Construcción de SQL ───────────────────────────────────────────────────────

_CREATED_TYPE_SQL = """
SELECT data_type
FROM information_schema.columns
WHERE table_name = 'CustomerPrizeWins' AND column_name = 'createdAt'
LIMIT 1;
"""


def _created_expr(created_type: str) -> str:
    """Expresión de createdAt en hora local, sin zona (no desplaza horas)."""
    if created_type == "timestamp with time zone":
        return '(cpw."createdAt" AT TIME ZONE %(tz)s)'
    return 'cpw."createdAt"'


def _catalog_values(catalog: list[dict]) -> tuple[str, dict]:
    rows, params = [], {}
    for i, c in enumerate(catalog):
        rows.append(f"(%(cid{i})s::int, %(pais{i})s::text, %(premio{i})s::text)")
        params[f"cid{i}"] = c["campaign_id"]
        params[f"pais{i}"] = c["pais"]
        params[f"premio{i}"] = c["premio"]
    return ",\n        ".join(rows), params


def _local_datetime(value: datetime, tz: str, with_timezone: bool) -> datetime:
    zone = ZoneInfo(tz)
    if value.tzinfo is not None:
        local = value.astimezone(zone)
    else:
        local = value.replace(tzinfo=zone)
    return local if with_timezone else local.replace(tzinfo=None)


def _where(flt: WinnersFilter, created_type: str) -> tuple[str, dict]:
    clauses, params = [], {"tz": flt.tz}
    with_timezone = created_type == "timestamp with time zone"
    created = 'cpw."createdAt"'

    if flt.desde:
        clauses.append(f"AND {created} >= %(desde)s")
        value = datetime.combine(flt.desde, time.min)
        params["desde"] = _local_datetime(value, flt.tz, with_timezone)
    if flt.hasta:
        clauses.append(f"AND {created} < %(hasta)s")
        value = datetime.combine(flt.hasta + timedelta(days=1), time.min)
        params["hasta"] = _local_datetime(value, flt.tz, with_timezone)
    if flt.hasta_exclusivo:
        clauses.append(f"AND {created} < %(hasta_exclusivo)s")
        params["hasta_exclusivo"] = _local_datetime(
            flt.hasta_exclusivo, flt.tz, with_timezone)
    if flt.inicios_campania:
        starts = []
        for campaign_id, start in sorted(flt.inicios_campania.items()):
            id_key = f"start_campaign_id_{campaign_id}"
            date_key = f"start_campaign_{campaign_id}"
            starts.append(
                f'(cpw."campaignId" = %({id_key})s AND {created} >= %({date_key})s)')
            params[id_key] = campaign_id
            params[date_key] = _local_datetime(start, flt.tz, with_timezone)
        clauses.append("AND (" + " OR ".join(starts) + ")")
    if flt.estados:
        clauses.append("AND cpw.status::text = ANY(%(estados)s)")
        params["estados"] = [str(e) for e in flt.estados]
    return "\n  ".join(clauses), params


WINNERS_SQL = """
WITH campanias (campaign_id, pais, premio) AS (
    VALUES
        {values}
)
SELECT
    ca.pais,
    ca.campaign_id AS campania_id,
    ca.premio AS tipo_premio,
    cp.name AS nombre_campania,

    cpw.id AS registro_ganador_id,
    cpw."customerId" AS cliente_id,

    CONCAT_WS(' ', c.first_name, c.last_name) AS nombre_completo,

    c.identification AS identificacion,
    c.telephone_number AS telefono,
    ci.email AS correo_electronico,

    cpw."totalPrizes" AS cantidad_premios,
    cpw.status AS estado,

    cpw."createdAt" AS fecha_hora_ganador,
    cpw."updatedAt" AS fecha_actualizacion,
    cpw."merchantId" AS comercio_id,

    venta.id AS transaccion_venta_id,
    venta."createdAt" AS fecha_venta,
    ip.id AS factura_id,
    ip.pos_invoice_id AS factura_pos,

    COALESCE(ip.store, venta.store) AS tienda_id,

    s.commercial_name AS nombre_tienda,
    s.public_name AS nombre_publico_tienda,

    ROUND(
        EXTRACT(
            EPOCH FROM (
                cpw."createdAt" - venta."createdAt"
            )
        )::numeric / 60,
        2
    ) AS minutos_entre_compra_y_premio,

    CASE
        WHEN venta.id IS NULL
            THEN 'SIN COMPRA CERCANA'
        WHEN ip.id IS NULL
            THEN 'COMPRA SIN FACTURA ENCONTRADA'
        WHEN COALESCE(ip.store, venta.store) IS NULL
            THEN 'COMPRA SIN TIENDA'
        ELSE 'COMPRA CANDIDATA - PENDIENTE VALIDAR'
    END AS validacion_tienda

FROM "CustomerPrizeWins" cpw
INNER JOIN campanias ca ON ca.campaign_id = cpw."campaignId"
LEFT JOIN campaigns cp  ON cp.id = cpw."campaignId"
LEFT JOIN customers c   ON c.id = cpw."customerId"
LEFT JOIN contactinfo ci ON ci.id = c."contactInfo"
-- "CustomerPrizeWins" no referencia transacción, factura ni tienda: la compra
-- se infiere como la última del mismo cliente en las 24 h previas al premio.
LEFT JOIN LATERAL (
    SELECT
        t.id,
        t.store,
        t.customer,
        t."createdAt",
        t.transaction_reference,
        t.transaction_reference_table
    FROM transactions t
    WHERE t.customer = cpw."customerId"
      AND t.transaction_type = 1
      AND t.transaction_reference_table = 'invoicepos'
      AND t."createdAt" <= cpw."createdAt"
      AND t."createdAt" >= (
          cpw."createdAt" - INTERVAL '24 hours'
      )
    ORDER BY
        t."createdAt" DESC,
        t.id DESC
    LIMIT 1
) venta ON TRUE
LEFT JOIN invoicepos ip
    ON ip.id = venta.transaction_reference
LEFT JOIN stores s
    ON s.id = COALESCE(ip.store, venta.store)
WHERE TRUE
  {where}
ORDER BY ca.campaign_id, cpw."createdAt" ASC, cpw.id ASC;
"""

# Control sin ningún JOIN: base para conciliar contra el detalle.
CONTROL_SQL = """
SELECT
    cpw."campaignId"                          AS campania_id,
    COUNT(*)                                  AS registros,
    COUNT(DISTINCT cpw."customerId")          AS clientes_unicos,
    COALESCE(SUM(cpw."totalPrizes"), 0)       AS total_premios,
    COUNT(*) FILTER (WHERE cpw."totalPrizes" IS NULL OR cpw."totalPrizes" <= 0)
                                              AS sin_premios,
    to_char(MIN({created}), 'YYYY-MM-DD HH24:MI:SS') AS primer_ganador,
    to_char(MAX({created}), 'YYYY-MM-DD HH24:MI:SS') AS ultimo_ganador
FROM "CustomerPrizeWins" cpw
WHERE cpw."campaignId" = ANY(%(ids)s)
  {where}
GROUP BY cpw."campaignId";
"""

CAMPAIGN_NAMES_SQL = "SELECT id, name FROM campaigns WHERE id = ANY(%(ids)s);"
CAMPAIGN_STARTS_SQL = """
SELECT id, begin_date
FROM campaigns
WHERE id = ANY(%(ids)s);
"""


# ── Ejecución ─────────────────────────────────────────────────────────────────

def _run(conn, sql: str, params: dict) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def _to_local_naive(s: pd.Series, tz: str) -> pd.Series:
    """Convierte a hora local sin zona (lo que se ve en Excel) sin desplazar
    las horas: si viene con zona se convierte a `tz`; si ya es local se deja igual."""
    vals = s.dropna()
    aware = (
        not vals.empty
        and (
            isinstance(s.dtype, pd.DatetimeTZDtype)
            or getattr(vals.iloc[0], "tzinfo", None) is not None
        )
    )
    if aware:
        return pd.to_datetime(s, utc=True).dt.tz_convert(tz).dt.tz_localize(None)
    return pd.to_datetime(s)


def _clean_text(s: pd.Series) -> pd.Series:
    """Texto sin espacios sobrantes; vacío -> None (no elimina filas)."""
    out = s.astype("object").where(s.notna(), None)
    return out.map(lambda v: (str(v).strip() or None) if v is not None else None)


def normalize_detail(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    df = df.copy()
    for col in ("fecha_hora_ganador", "fecha_actualizacion", "fecha_venta"):
        df[col] = _to_local_naive(df[col], tz)
    for col in ("nombre_completo", "identificacion", "telefono", "correo_electronico",
                "nombre_tienda", "nombre_publico_tienda", "factura_pos"):
        df[col] = _clean_text(df[col])
    for col in ("tienda_id", "transaccion_venta_id", "factura_id"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["minutos_entre_compra_y_premio"] = pd.to_numeric(
        df["minutos_entre_compra_y_premio"], errors="coerce")
    return df


@dataclass
class WinnersData:
    detail: pd.DataFrame
    control: pd.DataFrame
    campaign_names: dict[int, str]
    catalog: list[dict]
    flt: WinnersFilter


COUNT_BY_CAMPAIGN_SQL = """
SELECT "campaignId" AS campania_id, COUNT(*) AS ganadores
FROM "CustomerPrizeWins"
WHERE "campaignId" = ANY(%(ids)s)
GROUP BY "campaignId"
ORDER BY "campaignId";
"""


def check_connection(db: DatabaseConfig) -> pd.DataFrame:
    """Pruebas progresivas: SELECT 1 y conteo por campaña (solo lectura).
    Lanza excepción si la conexión o las consultas fallan."""
    with get_connection(db) as conn:
        one = _run(conn, "SELECT 1 AS ok;", {})
        if int(one.iloc[0, 0]) != 1:
            raise RuntimeError("SELECT 1 devolvió un valor inesperado.")
        return _run(conn, COUNT_BY_CAMPAIGN_SQL,
                    {"ids": [c["campaign_id"] for c in CAMPAIGN_CATALOG]})


def fetch_winners(db: DatabaseConfig, flt: WinnersFilter) -> WinnersData:
    """Ejecuta detalle, control y nombres de campaña en una sola conexión
    de solo lectura. Lanza excepción si algo falla."""
    catalog = flt.selected_catalog()
    if not catalog:
        raise ValueError("Los filtros de país/campaña no coinciden con ninguna campaña del catálogo.")

    with get_connection(db) as conn:
        ctype_df = _run(conn, _CREATED_TYPE_SQL, {})
        if ctype_df.empty:
            raise RuntimeError('No se encontró la columna "createdAt" en "CustomerPrizeWins".')
        created_type = ctype_df.iloc[0, 0]
        created = _created_expr(created_type)

        ids = [c["campaign_id"] for c in catalog]
        if flt.usar_inicio_campania:
            starts = _run(conn, CAMPAIGN_STARTS_SQL, {"ids": ids})
            found = {int(r.id): r.begin_date for r in starts.itertuples()}
            missing = sorted(set(ids) - set(found))
            without_start = sorted(cid for cid, value in found.items() if pd.isna(value))
            if missing or without_start:
                invalid = sorted(set(missing + without_start))
                raise RuntimeError(
                    "No se pudo determinar begin_date para las campañas: "
                    + ", ".join(map(str, invalid)))
            flt.inicios_campania = {
                cid: value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
                for cid, value in found.items()
            }

        where, wparams = _where(flt, created_type)
        values, vparams = _catalog_values(catalog)

        detail = _run(conn, WINNERS_SQL.format(values=values, where=where),
                      {**wparams, **vparams})
        control = _run(conn, CONTROL_SQL.format(created=created, where=where),
                       {**wparams, "ids": ids})
        names = _run(conn, CAMPAIGN_NAMES_SQL, {"ids": ids})

    logger.info("Ganadores: %d registros de detalle, %d campañas con datos.",
                len(detail), len(control))
    return WinnersData(
        detail=detail,
        control=control,
        campaign_names={int(r.id): r.name for r in names.itertuples()},
        catalog=catalog,
        flt=flt,
    )
