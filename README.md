# GP Reporting — Sistema de Reportería Automática

Sistema profesional de reportes automáticos para campañas **Gane Premios (GP)**,
que consolida métricas de trivias, premios y cupones de Honduras y El Salvador,
genera PDFs ejecutivos y los distribuye por Teams, Slack, Telegram o Email.

---

## Arquitectura

```
PostgreSQL (HN + SV)
        │
        ▼
  queries/          ← Capa de acceso a datos (11 consultas)
        │
        ▼
  reports/
  ├── alerts.py     ← Motor de detección de alertas
  ├── charts.py     ← Gráficos Matplotlib (6 tipos)
  └── pdf_generator.py  ← PDF con ReportLab
        │
        ▼
  notifications/
  └── senders.py    ← Teams · Slack · Telegram · Email
        │
        ▼
  scheduler.py      ← Ejecución periódica + hora fija diaria
```

---

## Instalación

### Opción A — Python directo

```bash
# 1. Clonar / copiar el proyecto
cd gp-reporting

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con credenciales reales
```

### Opción B — Docker

```bash
cp .env.example .env
# Editar .env con credenciales reales

docker compose up -d
docker compose logs -f
```

---

## Configuración (`.env`)

| Variable | Descripción | Ejemplo |
|---|---|---|
| `DB_HN_HOST` | Host PostgreSQL Honduras | `192.168.1.10` |
| `DB_HN_NAME` | Nombre de la base de datos | `db_honduras` |
| `DB_SV_*` | Igual para El Salvador | — |
| `TEAMS_WEBHOOK_URL` | Webhook de Teams (opcional) | `https://...` |
| `SLACK_WEBHOOK_URL` | Webhook de Slack (opcional) | `https://...` |
| `TELEGRAM_BOT_TOKEN` | Token del bot (opcional) | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | ID del chat/canal | `-100123456` |
| `EMAIL_USER` | Cuenta de envío (opcional) | `reporte@empresa.com` |
| `EMAIL_RECIPIENTS` | Destinatarios separados por coma | `jefe@empresa.com` |
| `REPORT_INTERVAL_MINUTES` | Frecuencia del reporte | `60` |
| `DAILY_REPORT_TIME` | Hora del resumen diario | `08:00` |
| `STOCK_ALERT_THRESHOLD` | Alerta de stock (fracción) | `0.10` (= 10%) |
| `TRIVIA_DROP_THRESHOLD` | Alerta de caída de trivias | `0.25` (= 25%) |

---

## Ejecución

```bash
# Reporte único (sin scheduler)
python main.py

# Scheduler continuo
python scheduler.py
```

### Reporte diario de premios (texto)

```powershell
.\.venv\Scripts\python.exe reporte_diario_premios.py
```

Muestra el resumen y solicita confirmación antes de enviarlo a Telegram. Las adjudicaciones
se cuentan desde `CustomerPrizeWins`; no equivalen a entrega física. “Premios programados
acumulados al cierre de hoy” suma `qty` de cada slot ganador una vez cuando `dateFrom` es
hoy o anterior. Incluye la programación del día completo, sin filtrar por `timeFrom`, y no
depende de `dateTo`. Es una suma de la configuración vigente, no un historial ni un saldo
pendiente; el proyecto no acredita si `qty` es cantidad original o restante.
“Programado para hoy” incluye únicamente slots con `dateTo` hoy: separa los nuevos
(`dateFrom` hoy) de los arrastrados (`dateFrom` anterior), estos últimos agrupados por fecha
original. Los slots cuya vigencia por fecha continúa después de hoy se muestran aparte y no
se suman a “Programado para hoy”. Estas métricas suman `qty` configurado; no son pendientes.
El reporte no usa `data/premios_quemados.json` para esas métricas.

### Reporte de programación de premios por país

```powershell
.\.venv\Scripts\python.exe reporte_premios_atrasados.py
.\.venv\Scripts\python.exe reporte_premios_atrasados.py --detalle
```

Muestra un dashboard breve en consola para Honduras, El Salvador y Guatemala; solo envía
a Telegram después de responder `s`. Las campañas se muestran siempre en cada país.
`--detalle` imprime localmente los slots, sus fuentes y los contadores en su nivel nativo.
La consulta toma `campaigns.push_data` y la hora del mismo corte SQL, filtra `isWinner`,
usa `rewards.title` vía `couponId` y valida `store.id` contra `stores`.

El resumen de programación suma `slot.qty` una vez por registro válido, por fecha de
inicio, y lo identifica como cantidad configurada. No se interpreta `qty` como saldo ni
se presentan fechas previas como pendientes. `storeQty` se mantiene en su nivel
cupón/tienda y `won` en su nivel agregado por cupón; la diferencia que aparece únicamente
en `--detalle` se calcula una vez por cupón con la fórmula de `CONFIG_GP_SQL`:
`stock_total = qty` si `isGlobal` es `true`, de lo contrario `stock_total = SUM(storeQty)`;
`diferencia = stock_total - won`. No se atribuye a tienda, fecha o atraso.

La fuente de la exportación de matriz con `entregados` y `disponibles` por registro no
está en este repositorio. Hasta contar con esa fuente, entregados, disponibles,
cumplimiento, movimientos diarios y atrasos permanecen “N/D”; el detalle de atrasos
indica que falta saldo por PDV/programación. No se estiman con `slot.qty`, `storeQty` ni
`won`. Si la fecha del corte es pasada, el reporte aclara que representa el estado actual
de la configuración al corte, no una captura histórica.

El reporte independiente con desglose por campaña se ejecuta con:

```powershell
.\.venv\Scripts\python.exe reporte_diario_premios_por_campania.py
```

Muestra totales por país y los mismos datos por campaña, en consola; no envía mensajes a
Telegram. Las cantidades de programación suman `qty` de la configuración vigente y las
adjudicaciones provienen de `CustomerPrizeWins`.

Para comparar los acumulados por fecha final e inicial y adjudicaciones de las campañas 825
y 826 al corte fijo `2026-11-12 10:26:00-06:00`, ejecutar el diagnóstico independiente:

```powershell
.\.venv\Scripts\python.exe diagnostico_acumulado_configuracion.py
```

El diagnóstico solo ejecuta SELECT, agrega la configuración vigente sin deduplicar filas y no
contacta Telegram. La salida no reconstruye configuraciones históricas.

### Exportar ganadores a Excel (campañas Gane Premios GT / SV / HN)

```bash
python export_ganadores.py                       # Excel completo en output/ganadores_gp_<fecha>.xlsx
python export_ganadores.py --preview             # vista previa en consola, sin archivo
python export_ganadores.py --pais GT SV --desde 2026-06-01 --hasta 2026-06-30 --estado 1
python export_ganadores.py --campania 818 825 --output output\ganadores.xlsx
```

Filtros opcionales (se aplican en PostgreSQL con parámetros): `--pais`, `--campania`,
`--desde`, `--hasta`, `--estado`. Usa `DB_HN_*` (misma BD que `DB_SV_*`), solo `SELECT`.

**Conexión:** igual que `main.py`, usa `.env` (`localhost:5433`). Ese puerto es un **túnel SSH**
a RDS que debes abrir antes, en otra terminal, y dejar corriendo:

```powershell
ssh -i <llave.pem> -L 5433:<host-RDS>:5432 <usuario>@<bastion> -N
Test-NetConnection localhost -Port 5433      # debe dar TcpTestSucceeded : True
python export_ganadores.py --check           # SELECT 1 + conteo por campaña
python export_ganadores.py --telegram        # genera, valida y envía el Excel al grupo de Telegram
                                             # (verifica que el destino sea un grupo y pide confirmación;
                                             #  --yes omite la pregunta; no aplica con --preview/--check)
```

`demo.py` usa datos ficticios y no requiere BD. La llave `.pem` debe ser legible solo por tu usuario
(OpenSSH en Windows la rechaza si no).
Las fechas se exportan en hora local (`--tz`, por defecto `America/Guatemala`).
El Excel incluye conciliación contra la BD; el comando devuelve código 1 si alguna
validación falla. `output/*.xlsx` está en `.gitignore` por contener datos personales.
El campo `status` se muestra como código, sin interpretarlo como entregado/canjeado.

---

## Contenido del PDF

| Sección | Contenido |
|---|---|
| **Portada** | Fecha, hora y resumen de alertas |
| **Resumen ejecutivo** | KPIs: premios hoy, trivias jugadas, correctas/incorrectas |
| **Alertas** | Críticas 🔴, advertencias 🟡, informativas 🔵 |
| **Rendimiento de Trivias** | Barras apiladas + tabla por país |
| **Premios Entregados Hoy** | Barras verticales + tabla por país |
| **Histórico y Promedio** | Total entregado + promedio diario |
| **Config GP** | Stock vs ganados, premios que faltan |
| **Reclamos** | Distribución de estados (pastel + tabla) |

---

## Alertas automáticas

| Alerta | Nivel | Condición |
|---|---|---|
| Premio agotado | 🔴 Crítica | `faltan_por_entregar <= 0` |
| Stock crítico | 🟡 Advertencia | Restante < umbral configurado |
| Error en entrega automática | 🔴 Crítica | `estado = "Error en entrega"` |
| Sin entregas hoy | 🟡 Advertencia | No hay registros del día |
| Premio sin entregas | 🔵 Info | ID sin movimiento en el día |

---

## Estructura de archivos

```
gp-reporting/
├── config/
│   ├── __init__.py
│   └── settings.py          ← Carga variables de entorno
├── queries/
│   ├── __init__.py
│   ├── trivias.py            ← Queries 01, 02, 03
│   └── premios.py            ← Queries 06–11
├── reports/
│   ├── __init__.py
│   ├── alerts.py             ← Motor de alertas
│   ├── charts.py             ← Gráficos Matplotlib
│   └── pdf_generator.py      ← Constructor del PDF
├── notifications/
│   ├── __init__.py
│   └── senders.py            ← Teams · Slack · Telegram · Email
├── utils/
│   ├── __init__.py
│   ├── db.py                 ← Conexiones PostgreSQL con reintentos
│   └── logger.py             ← Logging con rotación de archivos
├── output/                   ← PDFs generados
├── logs/                     ← Logs con rotación diaria
├── main.py                   ← Orquestador principal
├── scheduler.py              ← Ejecución programada
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Extensión del proyecto

Para agregar una nueva query o sección:

1. Añadir la función SQL en `queries/trivias.py` o `queries/premios.py`.
2. Añadir el gráfico correspondiente en `reports/charts.py`.
3. Añadir el método `add_*_section()` en `reports/pdf_generator.py`.
4. Llamar ambos desde `main.py` dentro de `run_report()`.

---

## Notas

- Todas las queries son de **solo lectura** (`readonly=True`).
- Si una query falla, devuelve DataFrame vacío y el reporte continúa.
- Los PDFs se guardan con marca de tiempo: `reporte_gp_20260610_0800.pdf`.
- Los logs rotan diariamente y se conservan 30 días.
