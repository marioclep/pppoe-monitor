# Cambios

Todo lo de abajo está en la rama `rediseno-interfaz`, que todavía **no** se
unió a `main` (incluye también `endurecimiento-produccion`).

## 2026-10-01

- Se verificó en una instalación de producción el primer reinicio mensual: a las 00:05 locales se
  cerraron los 2012 períodos de septiembre y se abrieron nuevos en cero. Los
  acumulados coinciden byte a byte con la suma de las muestras.
- Interfaz en español e inglés (ver 2026-09-30), aprobada por el usuario y
  desplegada en las tres instalaciones de producción (`e3fce20` y `bbe7253`,
  mismo código más documentación). Las tres siguen en español.

## 2026-09-30

Nueva instalación de producción, la tercera (Ubuntu 26.04, en `6f93917`).

Usuarios y roles: desplegado en Pruebas (`ff1b454`) y, después de la revisión del
usuario, en las tres de producción (`9d9ff38`). En las cuatro, el `admin`
que ya existía quedó como Completo.

| Commit | Cambio |
|---|---|
| `2a2742d` | Backend: **roles** `full` (Completo) y `readonly` (Solo lectura) en `users.role` (migración `8d4c2a7e5b19`; los usuarios existentes quedan `full`). `require_full` en todos los endpoints de escritura (403 para solo lectura). `GET /auth/me`, `POST /auth/change-password` y `/users` (listar, crear, cambiar rol, resetear contraseña y borrar). Nadie se borra a sí mismo y siempre queda al menos un usuario Completo. |
| `ed6086a`, `f2ff052`, `e6a1341` | **Interfaz en español e inglés**, elegible en Configuración (`language`, por instalación). Traducidos también los avisos de alerta y los mensajes de "Probar conexión". i18n propio sin librerías (215 textos; el build falla si falta una traducción). Fechas y números en el formato del idioma. En producción desde el 2026-10-01. |
| `dde7ce3`, `c2c4c9a` | **Alertas por descarga y por subida**, por separado (ya no se suman): un umbral global por dirección y uno por cliente y dirección (migración `3f6a9c2e8d41`). El aviso nombra al usuario PPPoE, el router, el consumo y el umbral. Umbrales por cliente desde su detalle; sección **Alertas** con las últimas 200. Probado en Pruebas con datos reales: un umbral global de subida de 700 GB disparó exactamente los 4 clientes que lo superaban. Desplegado en las cuatro (producción en `9e3ee39`, sin umbrales cargados). |
| `db17bc4` | **Nombre de la instalación** en Configuración (`site_name`, opcional, hasta 40 caracteres): título "Dashboard ACME" y pestaña "Monitor PPPoE · ACME". Desplegado en las cuatro (producción en `8623896`), con el nombre de cada una ya cargado. |
| `ff1b454` | Frontend: pantalla **Usuarios**, **Cambiar contraseña** en el menú lateral, usuario actual con etiqueta "Solo lectura", y Routers y Configuración sin botones para modificar en solo lectura. |

**Verificado:**
- Pasan los 303 tests del backend. Un test recorre todas las rutas de
  escritura de la app con un usuario de solo lectura y espera 403.
- La migración `8d4c2a7e5b19` se probó (subir, bajar y volver a subir) sobre
  una copia de la base de Pruebas.
- La interfaz se verificó solo con lint, build y pedidos a la API
  publicada, no en un navegador.

## 2026-09-28

Desplegado en las tres instalaciones de ese momento (Pruebas y dos de producción).

| Commit | Cambio |
|---|---|
| `7f57ef9` | Footer "Desarrollado por MKE Solutions - info@mkesolutions.net" en todas las pantallas, incluido el login. |
| `159fda1`, `b507426` | Página **Servidor**: historial de CPU, memoria y disco de la VM, con una muestra por minuto (tabla `server_stats`, migración `4e8b1f6a2c75`). El CPU se mide con una referencia única para todo el proceso, porque `psutil.cpu_percent()` guarda su referencia por hilo y en el scheduler daba casi siempre 0. |
| `db506c0` | Detalle de cliente: descarga y subida, actuales y del mes, en cuatro tarjetas con el color de su serie. |
| `2ed9370` | Dashboard: globo con cada sondeo de las últimas 24 h al pasar el cursor por un router. Endpoint `GET /dashboard/routers/{id}/polls`. |
| `9caa949` | Botón **⤢** para ampliar cualquier gráfico (`ChartPanel` + `ChartModal`). El globo de routers ahora acepta el cursor y también se puede ampliar. |
| `5bc9219` | Backend: en cada sondeo se lee además `/system/resource`, sin que su falla afecte al tráfico. Se guardan CPU, memoria y disco en `router_poll_stats` y el modelo, la versión y el uptime en `routers` (migración `6b2d9e4f1a83`). Endpoints nuevos: `GET /routers/{id}/overview` y `GET /dashboard/routers/{id}/history`. |
| `1a69aeb` | **Página por router** (`/routers/:id`): encabezado, tarjetas, gráficos de tráfico, conectados y recursos, y la tabla de sus clientes (`ClientsTable`, extraída de la página Clientes). |
| `100ebf1` | Tarjetas de la página del router en dos filas: CPU, memoria, disco y clientes conectados; después, el tráfico. |

**Verificado:**
- Pasan los 267 tests del backend.
- La migración `6b2d9e4f1a83` se probó (subir, bajar y volver a subir) sobre
  una copia de la base de Pruebas, sin perder filas.
- Los 21 routers reales de las tres instalaciones reportan sus recursos.
- Los cambios de interfaz se revisaron solo compilando y con las capturas del
  usuario, no con pruebas automáticas en un navegador.

**No soportado:** RouterOS v6, que no tiene API REST. Se decidió actualizar
esos routers a v7 en lugar de sumar la API clásica.
