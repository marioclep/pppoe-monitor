# Monitor PPPoE — contexto para Claude

Guía para trabajar en este repo con Claude Code (u otro asistente). Qué hace
el sistema y cómo se instala está en el [`README.md`](README.md); el
historial, en [`CHANGELOG.md`](CHANGELOG.md).

Si existe un `CLAUDE.local.md` (está en `.gitignore`), leerlo también: tiene
el contexto privado de cada desarrollador.

## Estructura

| Qué | Dónde |
|---|---|
| API (FastAPI) | `backend/app/api/` |
| Lógica: sondeo, deltas, resumen por hora, purga, reseteo mensual, alertas | `backend/app/services/` |
| Modelos (SQLAlchemy) y esquemas (Pydantic) | `backend/app/models/`, `backend/app/schemas/` |
| Migraciones | `backend/alembic/versions/` |
| CLI (`create-admin`, `reset-password`) | `backend/app/cli.py` |
| Tests del backend | `backend/tests/` |
| Pantallas y componentes (React + Vite) | `frontend/src/pages/`, `frontend/src/components/` |
| Textos de la interfaz | `frontend/src/i18n/es.ts` y `en.ts` |
| Backup diario | `backup/backup.sh` |
| Diseño y planes originales (históricos) | `docs/superpowers/` |

## Comandos

- Tests del backend: `cd backend && .venv/bin/pytest -q` (ver en el README
  qué base aceptan: nunca la de producción).
- Frontend: `cd frontend && npm run lint && npm run build`.
- Migración nueva: `cd backend && .venv/bin/alembic revision -m "..."` y
  después `.venv/bin/alembic upgrade head`. Probarla subiendo, bajando y
  volviendo a subir sobre una copia de una base real.

## Convenciones

- **Idioma:** la documentación y los mensajes de commit van en español, con
  el estilo `feat(frontend): ...`. El código y sus comentarios, en inglés.
- **Textos de la interfaz:** todo texto visible va en `frontend/src/i18n/es.ts`
  y `en.ts` (mismas claves; `en` está tipado contra `es`, así que el build
  falla si falta una traducción). En componentes se usa `useT()`; fuera de
  React, `t()` de `i18n/core`. Los textos del backend que llegan al usuario
  (avisos de alerta, "Probar conexión") usan `get_language(db)`.
- **TDD:** primero el test que falla y después la implementación.
- **Interfaz:** sin una herramienta de navegador, los cambios visuales se
  verifican con lint, build y pedidos a la API; decirlo explícitamente.

## Trampas conocidas

- **Comandos sueltos en el backend:** su `ENTRYPOINT` ignora los argumentos
  de `docker compose run`, así que hay que usar `--entrypoint`. Un
  `run backend alembic ...` levanta una segunda app entera.
- **`psutil.cpu_percent()`** guarda su referencia por hilo, y el scheduler
  corre cada tarea en cualquier hilo del pool. Por eso existe `CpuMeter` en
  `app/services/server_stats.py`.
- **`Row.t` de SQLAlchemy:** es un atributo propio de las filas, así que no
  sirve como alias de columna en SQL crudo. Usar, por ejemplo, `bucket_start`.
- **No agregar `--workers` a uvicorn:** con varios procesos el tráfico se
  cuenta varias veces.
- **RouterOS v6 no está soportado** (no tiene API REST). Se decidió no sumar
  la API clásica (puerto 8728).
- **Pedidos al router:** `/ppp/active` y `/system/resource` son opcionales.
  Si fallan, el sondeo sigue; nunca deben hacerlo fallar.
- **Solo lectura en los routers:** el monitor hace únicamente pedidos GET.
  No agregar nada que escriba en un router.
- **Unidades:** la interfaz muestra los tamaños en base 1024 con la etiqueta
  "GB" (`frontend/src/utils/format.ts`), y los umbrales de alerta usan la
  misma base (`frontend/src/utils/alerts.ts`).

## Problemas conocidos (menores)

- Un solo estado de error compartido en el dashboard.
- No hay control de orden en celular.
- Accesibilidad del menú lateral y de las filas de routers.
- Con cero routers se muestra "0 / 0 todos al día".
- Un gráfico con un solo punto se ve vacío.
- Los ids de gradiente de los gráficos se repiten.
- Los intervalos con valores nulos mezclados cuentan de menos a los
  conectados.
- Los gráficos por hora interpolan sobre los huecos en que el sistema estuvo
  apagado.
