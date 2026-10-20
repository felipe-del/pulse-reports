"""
queries/trivias.py
Todas las consultas relacionadas con trivias (queries 01, 02, 03).
"""
import pandas as pd
from config.settings import DatabaseConfig
from utils.db import query_df


def _ids_placeholder(ids: list[int]) -> str:
    return ",".join(str(i) for i in ids)


# ── Query 01 / 05: Rendimiento de Trivias ─────────────────────────────────────

RENDIMIENTO_SQL = """
WITH fo AS (
    SELECT id FROM form_dynamic WHERE id IN ({ids})
),
ppf AS (
    SELECT form, COUNT(id) AS total_preguntas
    FROM form_dynamic_question
    WHERE form IN (SELECT id FROM fo)
    GROUP BY form
),
tpc AS (
    SELECT
        fdac.form,
        fdac.customer,
        CASE
            WHEN SUM(fdac.trivia_is_correct) = MAX(ppf.total_preguntas) THEN 1
            ELSE 0
        END AS trivia_correcta
    FROM form_dynamic_answer_customer fdac
    JOIN ppf ON fdac.form = ppf.form
    GROUP BY fdac.form, fdac.customer
),
tp AS (
    SELECT
        NULLIF(c.push_data::JSONB -> 0 -> 'payload' ->> 'fid', '')::INT AS form_id,
        COUNT(DISTINCT t.id) AS enviados,
        SUM(CASE WHEN t.date_open IS NOT NULL THEN 1 ELSE 0 END) AS abiertos
    FROM campaigns c
    JOIN trackingpush t
        ON t.reference = c.id
        AND t.reference_table = 'campaigns'
    WHERE c.push_type = 'fp'
      AND NULLIF(c.push_data::JSONB -> 0 -> 'payload' ->> 'fid', '')::INT
          IN (SELECT id FROM fo)
    GROUP BY NULLIF(c.push_data::JSONB -> 0 -> 'payload' ->> 'fid', '')::INT
)
SELECT
    fd.id,
    REGEXP_REPLACE(fd.title, '^[^a-zA-ZáéíóúÁÉÍÓÚüÜñÑ]+', '') AS trivia,
    tp.enviados,
    tp.abiertos,
    COUNT(DISTINCT tpc.customer)                          AS veces_jugado,
    COUNT(CASE WHEN tpc.trivia_correcta = 1 THEN 1 END)   AS correctas,
    COUNT(CASE WHEN tpc.trivia_correcta = 0 THEN 1 END)   AS incorrectas
FROM form_dynamic fd
LEFT JOIN tpc ON fd.id = tpc.form
LEFT JOIN tp  ON fd.id  = tp.form_id
WHERE fd.id IN (SELECT id FROM fo)
GROUP BY fd.id, fd.title, tp.enviados, tp.abiertos
ORDER BY fd.id ASC;
"""

# ── Query 02: Detalle de Respuestas ───────────────────────────────────────────

DETALLE_SQL = """
WITH fo AS (
    SELECT id FROM form_dynamic WHERE id IN ({ids})
),
ppf AS (
    SELECT form, COUNT(id) AS total_preguntas
    FROM form_dynamic_question
    WHERE form IN (SELECT id FROM fo)
    GROUP BY form
),
tpc AS (
    SELECT
        fdac.form,
        fdac.customer,
        CASE
            WHEN SUM(fdac.trivia_is_correct) = MAX(ppf.total_preguntas) THEN 1
            ELSE 0
        END AS trivia_correcta
    FROM form_dynamic_answer_customer fdac
    JOIN ppf ON fdac.form = ppf.form
    GROUP BY fdac.form, fdac.customer
),
resp AS (
    SELECT
        form,
        customer,
        STRING_AGG(answers, ', ' ORDER BY questions ASC) AS respuestas
    FROM form_dynamic_answer_customer
    WHERE form IN (SELECT id FROM fo)
    GROUP BY form, customer
)
SELECT
    fd.id          AS form_id,
    fd.title       AS form_title,
    c.id           AS customer_id,
    c.first_name,
    c.last_name,
    ci.email,
    resp.respuestas,
    tpc.trivia_correcta
FROM form_dynamic fd
LEFT JOIN tpc  ON fd.id = tpc.form
LEFT JOIN resp ON (tpc.form = resp.form AND tpc.customer = resp.customer)
LEFT JOIN customers c    ON tpc.customer = c.id
LEFT JOIN contactinfo ci ON ci.id = c."contactInfo"
WHERE fd.id IN (SELECT id FROM fo)
ORDER BY fd.id ASC, c.last_name ASC;
"""

# ── Query 03: Resultado de Trivias (sin push) ─────────────────────────────────

RESULTADO_SQL = """
WITH fo AS (
    SELECT id FROM form_dynamic WHERE id IN ({ids})
),
ppf AS (
    SELECT form, COUNT(id) AS total_preguntas
    FROM form_dynamic_question
    WHERE form IN (SELECT id FROM fo)
    GROUP BY form
),
tpc AS (
    SELECT
        fdac.form,
        fdac.customer,
        CASE
            WHEN SUM(fdac.trivia_is_correct) = MAX(ppf.total_preguntas) THEN 1
            ELSE 0
        END AS trivia_correcta
    FROM form_dynamic_answer_customer fdac
    JOIN ppf ON fdac.form = ppf.form
    GROUP BY fdac.form, fdac.customer
)
SELECT
    fd.id,
    fd.title,
    COUNT(DISTINCT tpc.customer)                          AS usuarios_unicos,
    COUNT(CASE WHEN tpc.trivia_correcta = 1 THEN 1 END)   AS correctas,
    COUNT(CASE WHEN tpc.trivia_correcta = 0 THEN 1 END)   AS incorrectas
FROM form_dynamic fd
LEFT JOIN tpc ON fd.id = tpc.form
WHERE fd.id IN (SELECT id FROM fo)
GROUP BY fd.id, fd.title
ORDER BY fd.id ASC;
"""


def get_rendimiento(db: DatabaseConfig, ids: list[int]) -> pd.DataFrame:
    sql = RENDIMIENTO_SQL.format(ids=_ids_placeholder(ids))
    return query_df(db, sql)


def get_detalle(db: DatabaseConfig, ids: list[int]) -> pd.DataFrame:
    sql = DETALLE_SQL.format(ids=_ids_placeholder(ids))
    return query_df(db, sql)


def get_resultado(db: DatabaseConfig, ids: list[int]) -> pd.DataFrame:
    sql = RESULTADO_SQL.format(ids=_ids_placeholder(ids))
    return query_df(db, sql)