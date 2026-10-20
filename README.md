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
