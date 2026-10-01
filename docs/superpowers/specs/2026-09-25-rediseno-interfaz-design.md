# Rediseño de la interfaz y dashboard con gráficos — Diseño

Fecha: 2026-09-25
Estado: aprobado por el usuario en conversación (estilo, paneles y secciones 1 a 3). La spec está pendiente de revisión.

Extiende las specs anteriores (`2026-09-22-pppoe-monitor-design.md`,
`2026-09-24-sesiones-y-submuestreo-design.md`,
`2026-09-25-endurecimiento-produccion-design.md`). Lo que este documento no
menciona sigue como está.

Rama: `rediseno-interfaz`, creada desde `endurecimiento-produccion` (que
todavía no se fusionó en `main`).

## Objetivo

- Darle a la interfaz un aspecto de producto cuidado, con estética "pizarra
  moderna" (inspirada en Grafana) y modo claro y oscuro elegibles.
- Convertir el dashboard en un tablero con gráficos que muestren de un vistazo
  el estado de la red, en lugar de 3 tarjetas y una tabla.

### Decisiones del usuario

- **Estilo B, "pizarra moderna"** (elegido entre A Grafana clásico, B y C NOC):
  - barra lateral con nombres;
  - tarjetas y paneles redondeados;
  - gráficos de área con degradado;
  - barras de progreso en el ranking.
- Oscuro y claro, con un selector ☾/☀ que recuerda la elección.
- Se suma al rediseño visual un **dashboard con paneles nuevos**, en este orden:
  1. **Indicadores:** conectados ahora; descarga y subida ahora, cada una con
     el pico del rango elegido; routers al día (`N / M`).
  2. **Tráfico total** de todos los clientes en el rango elegido (24h, 7d, 30d
     o 90d).
  3. **Clientes conectados** en el tiempo.
  4. **Estado por router:** un punto de color, conectados y tráfico. Un click
     lleva a los clientes de ese router.
  5. **Top 10 consumidores del mes**, con barra proporcional. Un click lleva al
     detalle del cliente.
- La fuente de datos de los paneles 2 y 3 es la **opción 1**: una tabla chica de
  estadísticas por sondeo, con relleno inicial desde los datos existentes.

### Criterios de éxito

1. Todas las pantallas usan el nuevo estilo en ambos temas, sin texto
   ilegible ni colores fijos que no cambien con el tema.
2. El dashboard muestra los 5 paneles con datos reales y responde rápido en
   cualquier rango (una sola consulta liviana por panel).
3. Los gráficos cortan la línea donde el sistema no sondeó; ya no dibujan una
   rampa inventada.
4. La interfaz se puede usar en un celular: la barra lateral pasa a ser un
   menú y las tablas pasan a ser tarjetas.
5. La migración conserva todos los datos existentes y rellena la historia del
   dashboard.

### Fuera de alcance

- Paneles configurables o arrastrables.
- Alertas nuevas.
- Exportar datos.
- Internacionalización.
- Tests unitarios del frontend: el proyecto no tiene infraestructura para
  eso y no se agrega.

## 1. Frontend

### Tema y estilos

- CSS propio (sin Tailwind ni librerías de componentes) con **tokens en
  variables CSS** en `:root`.
- Paleta oscura de referencia:
  - fondo `#0f172a`;
  - lateral `#0b1222`;
  - paneles `#111c33`;
  - bordes `#1e293b`;
  - texto `#e2e8f0`;
  - acento `#60a5fa`.
- Paleta clara de referencia:
  - fondo `#f1f5f9`;
  - paneles y lateral `#ffffff`;
  - bordes `#e2e8f0`;
  - texto `#0f172a`;
  - acento `#3b82f6` (el azul activo del menú es `#1d4ed8`).
- Colores de series:
  - **Descarga**: azul (`#60a5fa` en oscuro, `#3b82f6` en claro).
  - **Subida**: rosa (`#f472b6` en oscuro, `#db2777` en claro).
  - **Conectados**: verde (`#34d399` en oscuro, `#059669` en claro).
- Colores de estado: verde, ámbar y rojo, cada uno con una variante para
  cada tema.
- **Selección del tema**:
  - Se aplica con `data-theme="light|dark"` en `<html>`.
  - Por defecto se sigue el tema del sistema (`prefers-color-scheme`).
  - El selector guarda la elección en `localStorage`. Todos los accesos están
    en try/catch; si fallan, se sigue el sistema.
  - Un script chico en `index.html` aplica el tema **antes** del primer
    render, para que no haya un destello del tema equivocado.
- **Tipografía**: pila del sistema (`system-ui, -apple-system, "Segoe UI",
  Roboto, sans-serif`), sin fuentes externas, porque el servidor puede no
  tener Internet. Los números de tarjetas y tablas usan
  `font-variant-numeric: tabular-nums`.
- **Íconos**: SVG propios en línea (un componente `Icon` con unos 8
  trazados), sin dependencias.

### Layout

- `Layout`:
  - **Barra lateral fija** de 200 px con la marca "Monitor PPPoE" y la
    navegación con ícono y nombre: Dashboard, Clientes, Routers,
    Configuración.
  - Abajo, el botón **Salir**.
  - Debajo de 800 px de ancho, la barra se oculta y aparece una barra superior
    con un botón ☰ que la abre como panel superpuesto.
- `PageHeader`: título, contenido opcional a la derecha (selector de rango,
  acciones) y el **selector de tema**, en todas las páginas autenticadas.

### Componentes reutilizables (`frontend/src/components/`)

| Componente | Qué hace |
|---|---|
| `Panel` | Contenedor con título opcional, acción a la derecha y cuerpo. |
| `StatCard` | Etiqueta, valor grande y subtítulo opcional. |
| `RangeSelector` | Grupo segmentado 24h / 7d / 30d / 90d que devuelve horas. |
| `ThemeToggle` | Alterna entre claro y oscuro; usa `useTheme`. |
| `StatusDot` | Punto de color con `title` accesible: ok / atrasado / sin respuesta. |
| `TrafficChart` | Gráfico de área Recharts con colores del tema, eje Y formateado y corte de huecos. |
| `Icon` | Íconos SVG en línea. |

Las tablas siguen siendo `<table>` con estilos nuevos y `data-label` para la
vista de tarjetas en celular, como ya ocurre hoy.

### Corte de huecos (`frontend/src/utils/gaps.ts`)

`withGaps(points, maxGapSeconds)`: entre dos puntos consecutivos separados
por más de `maxGapSeconds`, inserta un punto con los valores en `null`.
Recharts corta la línea ahí (`connectNulls={false}`). El umbral se define así:

- **Dashboard**: 2,5 × `bucket_seconds` que devuelve la API.
- **Detalle de cliente**:
  - puntos de 5 minutos (24h y 7d): 2,5 × el intervalo de sondeo;
  - puntos horarios (30d y 90d): 2,5 × 3600 s.
  El intervalo de sondeo sale de `polling_interval_seconds` del resumen del
  dashboard, así que el detalle también pide ese dato.

El gráfico de **consumo acumulado** del detalle no se corta: un total que
sigue plano durante un hueco es correcto.

### Refresco

- El dashboard vuelve a pedir resumen, historia y top cada **60 s**.
- Se pausa mientras la pestaña está oculta (`document.visibilityState`) y
  refresca apenas vuelve a estar visible.

### Estado de un router (en el frontend)

Con `intervalo = polling_interval_seconds` y la edad del último sondeo
exitoso:

| Estado | Condición | Color |
|---|---|---|
| ok | edad < 2 × intervalo | verde |
| atrasado | edad < 6 × intervalo | ámbar |
| sin respuesta | edad ≥ 6 × intervalo, o nunca se sondeó | rojo |

El indicador "Routers" muestra `ok / total`, y el subtítulo dice "todos al
día" o "N con problemas".

## 2. Backend

### Tabla `router_poll_stats`

| Columna | Tipo |
|---|---|
| `id` | PK |
| `router_id` | FK `routers.id` `ON DELETE CASCADE` |
| `polled_at` | timestamptz, not null |
| `clients_connected` | int, **nullable** (las filas rellenadas desde el resumen horario no tienen este dato) |
| `rx_bps` | bigint, not null |
| `tx_bps` | bigint, not null |

- Índices: `(polled_at)` y `(router_id, polled_at)`.
- **Escritura**: `poll_router` agrega una fila en la misma transacción del
  sondeo, justo antes del `commit`. `polled_at` es el mismo `now` que se
  guarda en `routers.last_polled_at`.
  - `clients_connected` es la cantidad de clientes activos del router después
    del sondeo, la misma que muestra el resumen.
  - `rx_bps` y `tx_bps` son la suma de las velocidades de las sesiones
    calculadas en ese sondeo.
  - Un sondeo que falla (`MikrotikError`) no escribe nada.
- **Purga**: `run_purge_job` borra las filas con `polled_at` anterior a
  `retention_days` (por defecto 90), en el mismo job y por lotes, como el
  resto.

### Migración (Alembic)

Crea la tabla y la rellena:

1. **Desde `traffic_samples`** (los últimos días de detalle): se agrupa por
   `(pppoe_clients.router_id, sampled_at)`.
   - `clients_connected` = cantidad de clientes distintos.
   - `rx_bps` y `tx_bps` = suma.
   - Los sondeos de un router comparten `sampled_at`, así que cada grupo es
     un sondeo.
2. **Desde `traffic_hourly`**, solo para las horas anteriores a la primera
   muestra de detalle de cada router: una fila por router y hora.
   - `polled_at` = `hour_start`.
   - `rx_bps` = suma de `rx_bytes` × 8 / 3600, y lo mismo para tx.
   - `clients_connected` = NULL.

El `downgrade` borra la tabla.

### `GET /dashboard/history?hours=N`

- Horas permitidas: 24, 168, 720 y 2160. Cualquier otro valor da 422.
- Ancho del intervalo (`bucket_seconds`) según el rango:

  | `hours` | `bucket_seconds` | Equivale a |
  |---|---|---|
  | 24 | 300 | 5 min |
  | 168 | 1800 | 30 min |
  | 720 | 7200 | 2 h |
  | 2160 | 21600 | 6 h |

- Solo cuenta los routers habilitados. Los intervalos se alinean con
  `date_bin(bucket, polled_at, '2000-01-01')`, en UTC.
- **Cálculo de cada intervalo**:
  1. Por cada router, el **promedio** de `rx_bps`, `tx_bps` y
     `clients_connected` dentro del intervalo. El promedio ignora los NULL.
  2. Se **suman** los routers.
  3. `clients_connected` es NULL solo si todos los routers lo tienen NULL en
     ese intervalo.
- **Respuesta**: `{"bucket_seconds": int, "points": [{"t": iso8601,
  "rx_bps": int, "tx_bps": int, "clients_connected": int | null}]}`, ordenada
  por `t`. Los intervalos sin datos no aparecen; el frontend los convierte en
  hueco.
- Requiere autenticación, como el resto de `/dashboard`.

### `GET /dashboard/summary` (se amplía)

Agrega dos campos:

- `polling_interval_seconds`: el valor de la configuración.
- `clients_seen_this_period`: cantidad de períodos de acumulación abiertos de
  clientes de routers habilitados, es decir, clientes vistos desde el último
  reseteo.

### Top 10

Usa el endpoint existente `GET /clients?sort_by=download&dir=desc&page_size=10`,
que ordena por acumulado de descarga del período. No hace falta un endpoint
nuevo.

## 3. Pantallas

| Pantalla | Cambios |
|---|---|
| **Login** | Tarjeta centrada con la marca, campos con etiqueta, botón de ancho completo, mensaje de error del estilo nuevo y selector de tema en una esquina. |
| **Dashboard** | Los 5 paneles en el orden aprobado, con el selector de rango en el encabezado, que por defecto está en 24h. |
| **Clientes** | Buscador, filtro por router (lee y escribe `?router=ID`), casilla "solo conectados", tabla con punto de estado y paginación con los estilos nuevos. Una fila clickeable lleva al detalle. |
| **Detalle de cliente** | Botón "← Clientes", título con usuario y router, tarjetas de velocidad actual y acumulado del mes, selector de rango y los dos gráficos (velocidad y acumulado) con `TrafficChart`. Los picos horarios se ven como línea punteada. |
| **Routers** | Formulario de alta y edición dentro de un `Panel`, tabla de routers con `StatusDot` y acciones como botones secundarios o de peligro. |
| **Configuración** | Secciones en paneles: sondeo y retención, reseteo mensual, SMTP, Telegram y umbrales de alerta. El comportamiento no cambia. |

Los textos de la interfaz se mantienen en español; solo cambia el diseño.

## Pruebas

### Backend (pytest)

- **Sondeo**:
  - un sondeo exitoso escribe una fila de `router_poll_stats` con los
    conectados y las sumas correctas;
  - un sondeo fallido no escribe ninguna fila.
- **Purga**: borra las estadísticas más viejas que la retención y conserva las
  recientes.
- **`/dashboard/history`**:
  - elige el intervalo correcto para cada rango;
  - promedia dentro del intervalo por router y suma entre routers;
  - un intervalo con NULL de un solo router mantiene los conectados de los
    otros;
  - excluye los routers deshabilitados;
  - devuelve 422 con horas no permitidas;
  - exige autenticación.
- **`/dashboard/summary`**: incluye `polling_interval_seconds` y
  `clients_seen_this_period`.
- **Migración**: el relleno se prueba contra una copia (`pg_dump`) de la base
  real de la VM:
  - las filas desde muestras coinciden con un conteo manual por sondeo;
  - las filas desde el resumen horario tienen `clients_connected` NULL;
  - no se pierde ningún dato existente.

### Frontend

- `npm run build` sin errores de TypeScript.
- Revisión manual en la VM de cada pantalla en ambos temas y a 375 px de
  ancho. Se le pide al usuario una mirada final en su navegador.
