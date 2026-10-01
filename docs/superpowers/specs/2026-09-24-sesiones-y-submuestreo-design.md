# Estado por sesión PPPoE y submuestreo de muestras — Diseño

Fecha: 2026-09-24
Estado: Aprobado por el usuario en conversación (partes 1–3), spec pendiente de revisión

Extiende `2026-09-22-pppoe-monitor-design.md`. Todo lo que este documento no
menciona sigue como está.

## Contexto y objetivo

Las pruebas contra un router real (CCR2004, RouterOS 7.24.2, ~230 clientes)
mostraron dos problemas que crecen con la escala:

1. **Sesiones que no se cuentan bien.**
   - RouterOS permite que un mismo usuario PPPoE tenga **varias sesiones
     simultáneas**. En el router de prueba, `castro.melina` y
     `moyano.julio` tienen dos cada uno, desde MAC consecutivas.
   - También puede nombrar la interfaz dinámica `<pppoe-usuario-1>` en lugar
     de `<pppoe-usuario>`. Pasa con `amado.lorena`.
   - Hoy el sistema guarda un solo estado por usuario, se queda con la
     sesión de menor uptime y *adivina* el nombre de la interfaz. El
     resultado:
     - Las sesiones con sufijo `-1` figuran con 0 de tráfico.
     - En los usuarios duplicados, el conteo acierta por casualidad. Con la
       asociación exacta, `castro.melina` pasaría a contar 2,8 MB en vez de
       73,7 GB.
2. **Crecimiento de `traffic_samples`.** Con 3.000 clientes, un sondeo cada
   5 minutos y 90 días de retención, son unos 78 millones de filas y ~15 GB,
   solo en muestras.

**Prioridad del usuario:** que el **acumulado del mes** de cada cliente sea
exacto. Los gráficos son secundarios.

**Criterios de éxito:**
- `castro.melina` y `moyano.julio` suman el tráfico de todas sus sesiones.
- `amado.lorena` registra tráfico.
- Con 3.000 clientes, las muestras ocupan ~2 GB en lugar de ~15 GB.
- La actualización conserva los datos de los routers ya cargados.

**Aceptado por el usuario:** al actualizar se pierde el tráfico de **un
intervalo de sondeo** (5 minutos). El estado previo se guarda por cliente y
no se puede convertir exactamente a estado por sesión.

## Parte 1 — Estado por sesión

### Lectura del router (`mikrotik_client.py`)

Las sesiones se leen de `GET /rest/interface/pppoe-server` y los contadores de
`GET /rest/interface`. Se deja de leer `/rest/ppp/active`.

- `/interface/pppoe-server` devuelve **una fila por sesión PPPoE activa**,
  con `user`, `uptime` y `name`. `name` es el nombre real de la interfaz
  dinámica, con o sin sufijo `-N`.
  - Verificado en el router real: tiene las mismas 233 sesiones que
    `/ppp/active`.
- `/interface` se indexa por `name`. De cada fila se toman `.id` (el ID
  interno de la interfaz), `rx-byte` y `tx-byte`.
- `MikrotikSession` pasa a tener estos campos: `username`, `uptime_seconds`,
  `interface_id`, `interface_name`, `rx_bytes` y `tx_bytes`.
  - Los contadores quedan en `None` si la interfaz no aparece en
    `/interface`, por ejemplo por una carrera entre los dos pedidos. Es la
    misma semántica que hoy.
- La decodificación UTF-8 con fallback a Latin-1 se mantiene para ambos
  endpoints.
- `check_connection` pasa a leer `/system/resource`,
  `/interface/pppoe-server` e `/interface`. La cantidad de sesiones sale de
  `/interface/pppoe-server`, así la prueba sigue verificando exactamente lo
  que necesita el poller.
- Se elimina `_match_interface_name`: ya no se adivinan nombres.

### Modelo: `session_state` por sesión

La tabla se reemplaza por una fila **por sesión**:

| Columna | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `router_id` | FK `routers`, `ON DELETE CASCADE` | |
| `client_id` | FK `pppoe_clients`, `ON DELETE CASCADE` | indexada |
| `interface_id` | `String(32)` | `.id` de RouterOS, p. ej. `*80020DCC` |
| `interface_name` | `String(128)` | solo para logs y diagnóstico |
| `last_uptime_seconds` | `Integer` | |
| `last_rx_bytes`, `last_tx_bytes` | `BigInteger` | |
| `last_poll_at` | `DateTime(tz)` | |
| `last_rx_bps`, `last_tx_bps` | `BigInteger`, default 0 | bps de la última lectura de esta sesión |

- Clave única: `(router_id, interface_id)`.
- **Por qué el `.id` y no el nombre:** el nombre se reutiliza cuando un
  cliente se desconecta y reconecta. Con `.id`, una sesión nueva es una fila
  nueva.
- Si un reinicio del router llegara a reutilizar un `.id`, la detección de
  "uptime que baja" la trata como sesión nueva. Ese mecanismo ya existe en
  `compute_delta`.
- **La velocidad actual de un cliente** es la suma de `last_*_bps` de sus
  sesiones. El dashboard y la lista de clientes la calculan así, agregando
  por `client_id`.

`traffic_samples` y `accumulation_periods` **no cambian de esquema**.

### Algoritmo del poller (`polling.py`)

Por cada router, en cada sondeo:

1. Leer las sesiones.
2. Agrupar las sesiones por `username`. A cada grupo le corresponde un
   `PPPoEClient`, que se crea si no existe y se marca activo.
3. Para cada sesión con contadores, buscar su estado por
   `(router_id, interface_id)`:
   - **Sin estado (sesión nueva):** se usa la regla actual de "primera vista".
     - Si `uptime <= segundos desde el sondeo anterior del router + un
       intervalo de margen`, cuentan todos sus bytes.
     - Si no, delta 0: los contadores quedan como base.
   - **Con estado:** delta = `compute_delta(...)` para rx y tx. Si el uptime
     bajó, se toma como sesión nueva y el tiempo transcurrido es su uptime.
   - Los bps se calculan por sesión y se actualiza su fila de estado.
4. Por cliente, **sumar los deltas y los bps de todas sus sesiones** y
   escribir:
   - una sola `TrafficSample`;
   - la suma en el `AccumulationPeriod` abierto.
   Después se evalúan las alertas una vez por cliente, como hoy.
   - Un cliente cuyas sesiones no tienen contadores en esta lectura queda
     activo y sin muestra, igual que hoy.
5. Borrar las filas de `session_state` del router cuyas sesiones no
   aparecieron en esta lectura. El router no da un `interface_id` para una
   sesión sin contadores, así que esas filas se identifican por
   `interface_name`, para no borrar el estado de una sesión que sigue viva.
6. Marcar inactivos los clientes del router sin ninguna sesión en esta
   lectura.

- Se elimina `_dedupe_sessions`.
- Se mantienen:
  - el bloqueo de la fila del router;
  - el descarte si el router se deshabilitó durante el sondeo;
  - el envío de notificaciones después del commit.
- Deshabilitar un router (`PUT /routers/{id}`) borra el `session_state` de
  sus sesiones, igual que hoy pero filtrando por `router_id`.

## Parte 2 — Resumen horario y purga

### Tabla `traffic_hourly`

| Columna | Tipo | Notas |
|---|---|---|
| `client_id` | FK `pppoe_clients`, `ON DELETE CASCADE` | PK compuesta |
| `hour_start` | `DateTime(tz)` | inicio de la hora en UTC, PK compuesta |
| `rx_bytes`, `tx_bytes` | `BigInteger` | suma de deltas de la hora |
| `peak_rx_bps`, `peak_tx_bps` | `BigInteger` | máximo de los `*_bps` de las muestras de la hora |

### Job de resumen (`services/rollup.py`)

- Corre **cada hora al minuto 2** y **una vez al arrancar**, para ponerse al
  día.
- Guarda una marca de agua en `app_settings` con la clave
  `hourly_rollup_until`: un instante UTC exclusivo, siempre al inicio de una
  hora.
- Procesa desde la marca (o desde la muestra más antigua si no hay marca)
  hasta el **inicio de la hora actual**. La hora en curso nunca se resume.
- Para ese rango hace:

  ```sql
  INSERT INTO traffic_hourly (client_id, hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps)
  SELECT client_id, date_trunc('hour', sampled_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
         SUM(rx_bytes_delta), SUM(tx_bytes_delta), MAX(rx_bps), MAX(tx_bps)
  FROM traffic_samples WHERE sampled_at >= :desde AND sampled_at < :hasta
  GROUP BY 1, 2
  ON CONFLICT (client_id, hour_start) DO UPDATE SET <todas las columnas> = EXCLUDED.<columna>
  ```

  Después avanza la marca de agua **en la misma transacción**.
- **Se puede repetir sin duplicar:** cada hora se recalcula completa y
  reemplaza a la anterior, nunca se suma encima. Las muestras siempre se
  escriben con `sampled_at = now`, así que una hora cerrada no recibe
  muestras nuevas.

### Purga (`purge.py`, diaria como hoy)

- **Muestras de 5 minutos:** se borran las que tienen `sampled_at` anterior a
  `min(ahora − raw_retention_days, hourly_rollup_until)`. **Nunca se borra una
  muestra que no esté resumida.**
  - Si no hay marca de agua, no se borran muestras.
- **Resumen horario:** se borran las filas con `hour_start` anterior a
  `ahora − retention_days`.
- Ambas se borran **en lotes de 10.000 filas** (`DELETE … WHERE id IN (SELECT
  … LIMIT 10000)`, o por clave en `traffic_hourly`), con un commit por lote.

### Configuración

- Nueva clave **`raw_retention_days`**, con 7 días por defecto.
- **`retention_days`** (90) pasa a aplicarse al resumen horario.
- Validación en `PUT /settings`: `1 <= raw_retention_days <= retention_days`.
  Si no se cumple, responde 422.
- `hourly_rollup_until` es interna: no se expone en la API.

### Tamaño estimado (3.000 clientes)

| | Filas | Tamaño |
|---|---|---|
| 5 min × 7 días | ~6 millones | ~1,1 GB |
| Horario × 90 días | ~6,5 millones | ~0,7 GB |

## Parte 3 — API, interfaz y migración

### API

- **`GET /clients/{id}/history?hours=N`**, con `1 <= N <= 2160` (90 días):
  - **Si `N <= raw_retention_days × 24`:** muestras de 5 minutos, como hoy.
    `peak_rx_bps` y `peak_tx_bps` van en `null`.
  - **Si no, un punto por hora:**
    - Las horas anteriores a `hourly_rollup_until` salen de `traffic_hourly`.
    - Las horas posteriores se agregan al vuelo desde `traffic_samples`, con
      la misma consulta que usa el job de resumen.
    - En estos puntos, `rx_bps`/`tx_bps` son el **promedio** de la hora
      (`bytes × 8 / 3600`; para la hora en curso, sobre los segundos
      transcurridos) y `peak_*_bps` es el máximo.
    - `rx_bytes_delta`/`tx_bytes_delta` son los bytes de la hora.
- **Dashboard y `GET /clients`:** la velocidad actual pasa a ser la suma de
  las sesiones de cada cliente.
  - El orden `sort_by=current` usa esa suma.
  - Un cliente inactivo muestra 0, porque no tiene sesiones.
- **`GET /settings` y `PUT /settings`:** incluyen `raw_retention_days`.

### Interfaz

- **Detalle del cliente:**
  - Rangos: 24 h, 7 días, 30 días y 90 días.
  - En los rangos por hora, el gráfico de velocidad muestra el promedio en
    línea continua y el pico en línea punteada.
  - El gráfico de consumo acumulado no cambia: es la suma de los deltas.
- **Configuración:** nuevo campo "Retención de detalle (días)", con la
  validación mostrada en pantalla.

### Migración (Alembic)

1. Crear `traffic_hourly`.
2. Borrar y recrear `session_state` con el esquema nuevo. El estado anterior
   se descarta: es la pérdida de un intervalo, ya aceptada.
3. Sembrar `raw_retention_days = 7`.
4. **No tocar** `accumulation_periods` ni `traffic_samples`.
5. `downgrade`: recrear `session_state` con el esquema anterior (vacía) y
   borrar `traffic_hourly` y la clave nueva.

- El job de resumen, al arrancar, procesa las muestras existentes.

**Verificación obligatoria antes de aplicar en la VM:**
- Migración sobre una base nueva vacía.
- Migración sobre **una copia de la base real** de la VM (`pg_dump` →
  base temporal):
  - `rx_bytes_total`/`tx_bytes_total` de cada período abierto deben ser
    idénticos antes y después;
  - después del resumen, la suma de bytes de `traffic_hourly` por cliente
    debe coincidir con la suma de `traffic_samples` de las horas cerradas.

## Pruebas (TDD)

- **`mikrotik_client`:**
  - sesiones desde `/interface/pppoe-server`;
  - nombre con sufijo `-1`;
  - dos sesiones del mismo usuario;
  - sesión sin fila en `/interface`, que da contadores en `None`;
  - `check_connection` cuenta sesiones de `pppoe-server`.
- **Poller:**
  - dos sesiones del mismo usuario se suman en la muestra y en el período;
  - una sesión se cae mientras la otra sigue: su estado se borra y el
    cliente sigue activo;
  - reconexión con uptime menor;
  - `.id` reutilizado con uptime menor;
  - el cliente queda inactivo solo sin sesiones;
  - sesión sin contadores: su estado no se borra;
  - la regla de "primera vista" funciona por sesión.
- **Resumen:**
  - suma y pico por hora;
  - la hora en curso no se resume;
  - repetir no duplica;
  - la marca de agua avanza.
- **Purga:**
  - respeta la marca de agua;
  - aplica las dos retenciones;
  - borra por lotes.
- **API:**
  - historial corto (5 min) y largo (horario, con la hora en curso al vuelo);
  - validación de `hours`;
  - velocidad actual como suma de sesiones;
  - validación de `raw_retention_days`.
- **En vivo contra los routers de la VM:**
  - `castro.melina` y `moyano.julio` suman sus dos sesiones;
  - `amado.lorena` tiene tráfico;
  - los acumulados del mes se conservan tras la migración.

## Fuera de alcance

- El retoque estético de la interfaz, que tendrá su propio diseño.
- Particionar tablas o usar TimescaleDB. Se evaluaron y no hacen falta a
  esta escala; se puede migrar después sin cambiar la API.
- Mostrar en la interfaz el detalle de cada sesión de un cliente. El
  acumulado es por cliente.
