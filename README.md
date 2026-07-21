# Radar de Relojes — Inteligencia de compras para reventa

Sistema automático que monitorea diariamente los precios de tus relojes en
Amazon, detecta mínimos históricos y te avisa por Gmail cuándo comprar.

---

## 1. Arquitectura

```
                    ┌──────────────────────────┐
                    │   data/watches.csv       │  ← TÚ editas esto
                    │   (repositorio de links) │     (30 relojes)
                    └────────────┬─────────────┘
                                 │
   GitHub Actions (cron diario)  ▼
   ┌────────────────────────────────────────────────────┐
   │  src/providers/  →  amazon_scraper  |  keepa       │
   │  Capa intercambiable: cambias de fuente con 1 var  │
   └────────────────────────┬───────────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │  data/prices.db      │  histórico versionado en git
                 │  (SQLite)            │  products / price_history / alerts
                 └──────────┬───────────┘
                            ▼
              ┌───────────────────────────────┐
              │  src/analyze.py               │
              │  mínimos 7d/30d/histórico     │
              │  volatilidad · tendencia      │
              │  score 0-100 → recomendación  │
              └──────────┬────────────────────┘
                         ▼
     ┌───────────────────────────────────────────┐
     │  src/notify.py + templates/*.html         │
     │  Gmail SMTP → alertas · semanal · mensual │
     └───────────────────────────────────────────┘
```

**Costo total: $0.** GitHub Actions da 2.000 minutos/mes gratis; este sistema
usa ~90. Gmail SMTP es gratis. SQLite no tiene servidor.

### ¿Por qué esta fuente de datos?

| Opción | Costo | Histórico | Veredicto |
|---|---|---|---|
| **Scraper propio** ✅ | $0 | Se construye día a día | **Elegido.** Suficiente para 30 productos |
| Keepa API | ~€19/mes | Años, desde el día 1 | Ya integrado: actívalo con `PRICE_PROVIDER=keepa` |
| Amazon PA-API | $0 | **No tiene** | Descartado: exige 3 ventas como afiliado y no da histórico |

El sistema nace "ciego" y gana precisión con el tiempo: a los 7 días detecta
mínimos semanales, a los 14 mínimos históricos, al mes ya calcula tendencias
y volatilidad reales. Si algún día quieres histórico profundo inmediato,
contratas Keepa y cambias **una variable** — el resto del código no se toca.

---

## 2. Estructura del repositorio

```
relojes/
├── data/
│   ├── watches.csv          ← EL repositorio de links (lo editas tú)
│   └── prices.db            ← histórico de precios (automático)
├── src/
│   ├── main.py              ← CLI: track / weekly / monthly / add / list
│   ├── db.py                ← esquema SQLite + lectura del CSV
│   ├── analyze.py           ← mínimos, score, recomendación, tendencias
│   ├── notify.py            ← envío Gmail SMTP
│   ├── site.py              ← genera los datos del dashboard web
│   ├── config.py
│   └── providers/           ← amazon_scraper.py · keepa.py (intercambiables)
├── docs/
│   ├── index.html           ← EL DASHBOARD (ábrelo con doble clic)
│   ├── cargar.html          ← PANTALLA PARA PEGAR TUS LINKS
│   └── data.js              ← datos que la alimentan (automático)
├── templates/               ← diseño de los correos (HTML)
├── reports/                 ← copia local de cada alerta y reporte
├── .github/workflows/       ← daily.yml · reports.yml
├── config.yaml              ← TODAS las reglas ajustables
└── .env                     ← credenciales (nunca se sube)
```

### El repositorio de links: `data/watches.csv`

```csv
asin,name,url,target_price,active,category,notes
B0CHX1W1XY,Seiko 5 SRPD55,https://www.amazon.com/dp/B0CHX1W1XY,185.00,true,automatico,el que más rota
```

| Columna | Obligatoria | Para qué sirve |
|---|---|---|
| `asin` | No — se deduce del URL | Identificador único del producto |
| `name` | Sí | Nombre corto que verás en correos |
| `url` | Sí | Link de Amazon |
| `target_price` | No | Si el precio baja de aquí, alerta inmediata |
| `active` | No (default `true`) | `false` deja de monitorearlo sin perder su historia |
| `category` / `notes` | No | Tu organización interna |

**La forma fácil de cargarlos:** abre `docs/cargar.html` con doble clic, pega los
30 links de una sola vez y descarga el `watches.csv` ya armado. Detecta el ASIN
solo, descarta duplicados y avisa qué líneas no sirven.

**Agregar un reloj nuevo:** pega una fila más, o usa
`python -m src.main add https://www.amazon.com/dp/XXXX --target 150`
(lee el nombre desde Amazon automáticamente).
**Quitar uno:** pon `active,false`. El histórico se conserva.

---

## 3. Reglas de alerta y motor de recomendación

Una alerta se dispara cuando ocurre **cualquiera** de estas (configurables en `config.yaml`):

| Regla | Condición | Peso en el score |
|---|---|---|
| `min_all_time` | Precio = mínimo histórico | 45 |
| `target_price` | Precio ≤ tu objetivo | 30 |
| `min_30d` | Precio = mínimo de 30 días | 25 |
| `daily_drop` | Cae >5% vs ayer | 15 |
| `min_7d` | Precio = mínimo de 7 días | 12 |

Más bonus por cercanía al mínimo histórico (+15) y por descuento vs el precio
habitual/mediana (+15). Score final 0-100:

- **≥ 60 → COMPRAR AHORA**
- **30-59 → MONITOREAR**
- **< 30 → ESPERAR**
- Sin precio legible → **REVISAR MANUALMENTE**

Salvaguardas contra falsas alarmas:
- `cooldown_days: 3` — no repite la misma alerta 3 días seguidos.
- `tie_tolerance_pct: 0.5` — cuenta como mínimo si está a ≤0.5% del mínimo.
- `min_abs_change_usd: 0.50` — ignora ruido de centavos.
- No declara "mínimo histórico" hasta tener 14 días de historia.

---

## 4. Puesta en marcha

### Paso 1 — Una cuenta de Gmail solo para el bot

Enviar correo exige autenticarse: no basta con conocer la dirección de destino.
Por eso el sistema necesita una cuenta desde la cual enviar.

**Usa una cuenta nueva, no la personal.** La contraseña de aplicación no permite
entrar a la web de Google, pero sí leer ese buzón por IMAP. Si la cuenta se creó
solo para esto y está vacía, una filtración no cuesta nada.

1. Crea un Gmail nuevo, p. ej. `radar.relojes.bot@gmail.com`.
2. Actívale la verificación en 2 pasos: <https://myaccount.google.com/security>
3. Genera la contraseña de aplicación: <https://myaccount.google.com/apppasswords> —
   nómbrala `Radar Relojes` y copia los 16 caracteres.
4. En `ALERT_TO` pones **tu correo personal**: ahí es donde recibes.

> Esa contraseña la escribes tú en `.env` o en los Secrets de GitHub. No se comparte
> con nadie más ni viaja fuera de tu máquina.

### Paso 2 — Sube el proyecto a GitHub

```bash
cd relojes
git init && git add . && git commit -m "Radar de relojes"
gh repo create relojes --private --source=. --push
```
> Créalo **privado**: contiene tu estrategia de precios.

### Paso 3 — Carga los secretos

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|---|---|
| `GMAIL_USER` | la cuenta **nueva** del bot (envía) |
| `GMAIL_APP_PASSWORD` | los 16 caracteres del paso 1 |
| `ALERT_TO` | tu correo personal (recibe) |

### Paso 4 — Prueba

**Actions → Monitoreo diario de precios → Run workflow.** Si llega el correo, listo:
a partir de ahí corre solo todos los días a las 7:15 a.m. (Colombia).

### Correr en tu PC (opcional)

Necesitas Python 3.11+ — **no está instalado en este equipo**
(<https://www.python.org/downloads/> · marca *Add Python to PATH*).

```bash
pip install -r requirements.txt
copy .env.example .env        # y llena las credenciales
python -m src.main track --no-email   # prueba sin enviar correo
python -m src.main list               # tabla en consola
```

---

## 5. La página web (dashboard)

`docs/index.html` es tu panel de control. **Ábrelo con doble clic** — no necesita
servidor ni internet. Trae:

- 4 indicadores arriba: monitoreados, en zona de compra, en mínimo histórico y
  cuánto ahorras si compras hoy todo lo que está en oferta.
- Tabla ordenable con mini-gráfica de 30 días por reloj, variación semanal y veredicto.
- Buscador y filtros por veredicto (Comprar / Monitorear / Esperar).
- Clic en cualquier fila → ficha con gráfica grande, crosshair que sigue el mouse,
  los 8 indicadores del producto, por qué se activó la alerta y botón a Amazon.
- Tema claro/oscuro (recuerda tu elección).

Se actualiza sola: cada corrida diaria reescribe `docs/data.js`. También puedes
forzarla con `python -m src.main site` (no vuelve a consultar Amazon, solo
recalcula con lo que ya hay en la base).

**Para verla desde el celular**, publícala en GitHub Pages:
*Settings → Pages → Source: Deploy from a branch → Branch: `main` / carpeta `/docs`*.
Queda en `https://TU-USUARIO.github.io/relojes/`.
> Si tu repo es privado, Pages requiere plan de pago. Alternativa gratis: abre el
> archivo desde tu PC, o haz el repo público — no contiene credenciales
> (viven en Secrets), solo links y precios.

---

## 6. Comandos

| Comando | Qué hace | Cuándo corre solo |
|---|---|---|
| `python -m src.main track` | Scrapea, guarda, analiza, alerta | Diario 7:15 a.m. |
| `python -m src.main weekly` | Reporte semanal consolidado | Lunes 8:00 a.m. |
| `python -m src.main monthly` | Análisis profundo del mes | Día 1, 8:30 a.m. |
| `python -m src.main add URL` | Agrega un link al repositorio | Manual |
| `python -m src.main list` | Estado actual en consola | Manual |
| `python -m src.main export` | Vuelca el histórico a CSV (Excel) | Manual |

Cualquiera acepta `--no-email` para generar el HTML sin enviarlo.

---

## 7. Qué contiene cada correo

**Alerta diaria** — una tarjeta por reloj, ordenadas por score: nombre, precio
actual grande, precio anterior tachado, % vs ayer, mínimos 7d/30d/histórico con
sus fechas, descuento vs precio habitual, tu precio objetivo, días de historial,
veredicto (COMPRAR AHORA / MONITOREAR / ESPERAR) y botón directo a Amazon.

**Reporte semanal** — 3 métricas de cabecera (monitoreados / en zona de compra /
en mínimo histórico), ranking Top-10 de oportunidades, los 5 que más bajaron,
los 5 que más subieron, y la tabla completa de los 30.

**Análisis mensual** — tendencia por producto (regresión lineal: subiendo /
estable / bajando con % semanal), volatilidad, el **día de la semana** en que
cada reloj históricamente está más barato, y dos bloques de decisión de
inventario: *comprar en volumen* y *evitar por ahora*.

---

## 8. Si algo falla

| Síntoma | Causa y solución |
|---|---|
| `bloqueado por CAPTCHA` | Amazon detectó el bot. Sube `delay_min/max_seconds` en `config.yaml` a 8-20. Si persiste, activa Keepa. |
| `precio no encontrado` | Amazon cambió el HTML o el producto no tiene Buy Box. Revisa el link a mano. |
| No llega el correo | Verifica que sea la contraseña **de aplicación**, no la normal. Revisa Spam. |
| Alertas de más | Sube `daily_drop_pct` o `cooldown_days` en `config.yaml`. |
| Alertas de menos | Baja `buy_now_score` a 50 y define `target_price` en el CSV. |

**Sobre el scraping:** consultar ~30 páginas al día con pausas de 3-9 segundos es
un volumen mínimo, pero técnicamente los Términos de Servicio de Amazon lo
restringen. Si te bloquean de forma recurrente, la ruta limpia es Keepa
(ya integrado, un cambio de variable).
