# Tacerca automation

## Archivos
- `monitor_tacerca.py`: script principal, con monitoreo + auto-reserva.
- `monitor_tacerca_backup.py`: respaldo del monitor original, sanitizado.
- `.env.example`: plantilla de variables.
- `requirements.txt`: dependencias Python.
- `.gitignore`: excluye secretos y estado local.

## Instalación
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

## Ejecución
```bash
python3 monitor_tacerca.py
```

## Monitor único 29-04-2026 06:54 (Los Montes -> Escuela Militar)
Para este caso puntual, usa:

```bash
python3 monitor_tacerca_29042026_0654.py
```

Este script:
- Monitorea sólo la fecha `29-04-2026`, ruta `Los Montes -> Escuela Militar`, horario `06:54`.
- Si detecta cupo, intenta reservar de inmediato a tu nombre.
- Fuerza pago con `wallet`.
- Si no encuentra cupo, avisa por Telegram cada `1800` segundos (30 minutos).

Variables opcionales:
- `TACERCA_2904_POLL_SECONDS` (default `60`)
- `TACERCA_2904_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS` (default `1800`)

Ejecución continua en segundo plano:

```bash
nohup python3 -u monitor_tacerca_29042026_0654.py </dev/null >> monitor_29042026.log 2>&1 &
```

## Monitor único 10-06-2026 06:54 (Piedra Roja / Los Montes -> Escuela Militar)
Para este caso puntual, usa:

```bash
python3 monitor_tacerca_09062026_0654.py
```

Este script:
- Monitorea sólo la fecha `10-06-2026`, ruta `Piedra Roja / Los Montes -> Escuela Militar`, horario `06:54`.
- Revisa disponibilidad cada `300` segundos por defecto.
- Si detecta cupo, intenta reservar de inmediato a nombre del titular.
- Fuerza pago con `wallet`.
- Si no encuentra cupo, avisa por Telegram al menos cada `3600` segundos (1 hora).

Variables opcionales:
- `TACERCA_1006_POLL_SECONDS` (default `300`)
- `TACERCA_1006_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS` (default `3600`)

Ejecución continua en segundo plano:

```bash
nohup python3 -u monitor_tacerca_09062026_0654.py </dev/null >> monitor_10062026.log 2>&1 &
```

## Monitor único Agosto 2026 06:35 (Piedra Roja / Los Montes -> Escuela Militar, 2 pasajeros)
Para reservar solo ida, en el horario `06:35`, para el titular (asiento 7) y el pasajero
frecuente Teodoro Iribarren (asiento 8), en las fechas `03-08-2026` y `17-08-2026`, usa:

```bash
python3 monitor_tacerca_agosto2026_0635.py
```

Este script:
- Monitorea solo las fechas `03-08-2026` y `17-08-2026`, ruta ida Piedra Roja / Los Montes -> Escuela Militar, horario `06:35`.
- Revisa disponibilidad cada `600` segundos (10 minutos) por defecto.
- Reserva ambos asientos (titular + Teodoro Iribarren) apenas detecta cupo, vía `trips/create-trip-massive/`.
- Fuerza pago con `wallet`.
- Si no encuentra cupo, avisa por Telegram al menos cada `3600` segundos (1 hora).
- Si encuentra y reserva, avisa por Telegram.
- Estado local: `tacerca_state_agosto2026_0635_wallet.json`.

Variables opcionales:
- `TACERCA_AGO_POLL_SECONDS` (default `600`)
- `TACERCA_AGO_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS` (default `3600`)

Ejecución continua en segundo plano:

```bash
nohup python3 -u monitor_tacerca_agosto2026_0635.py </dev/null >> monitor_agosto2026.log 2>&1 &
```

## Monitor único 03-09-2026 06:35 / 06:55 / 08:25 (Piedra Roja / Cond. Los Montes -> Metro Escuela Militar)
Para monitorear solo estos horarios en la fecha `03-09-2026`, usa:

```bash
python3 monitor_tacerca_03092026_0635_0655_0825.py
```

Este script:
- Monitorea solo la fecha `03-09-2026`, ruta `Piedra Roja / Cond. Los Montes -> Metro Escuela Militar`.
- Revisa disponibilidad cada `300` segundos por defecto.
- Si encuentra al menos `1` asiento disponible en cualquiera de los horarios `06:35`, `06:55` u `08:25`, avisa por Telegram.
- Si no encuentra asientos, no envía avisos.
- Usa la sesión autenticada del usuario configurado en `TACERCA_USERNAME` y `TACERCA_PASSWORD`.
- Estado local: `tacerca_state_03092026_0635_0655_0825.json`.

Variable opcional:
- `TACERCA_03092026_POLL_SECONDS` (default `300`)

Ejecución continua en segundo plano:

```bash
nohup python3 -u monitor_tacerca_03092026_0635_0655_0825.py </dev/null >> monitor_03092026.log 2>&1 &
```

## Ejecución continua local
Para dejar el monitor corriendo aunque cierres la terminal, define el modo en `.env`:

```bash
TACERCA_RUN_MODE=3
```

Si usas `TACERCA_RUN_MODE=3`, como ese modo puede crear reservas reales, agrega también una confirmación explícita:

```bash
TACERCA_MAY_PLAN_CONFIRM=true
```

Luego ejecútalo en segundo plano con logs:

```bash
nohup python3 -u monitor_tacerca.py </dev/null >> monitor.log 2>&1 &
```

Comandos útiles:

```bash
tail -f monitor.log
pgrep -af monitor_tacerca.py
pkill -f monitor_tacerca.py
```

En macOS, si el computador entra en reposo el proceso se pausa. Para mantenerlo despierto mientras corre:

```bash
nohup caffeinate -dimsu python3 -u monitor_tacerca.py </dev/null >> monitor.log 2>&1 &
```

El script primero preguntará el modo:
- `1`: flujo actual de monitor / reserva puntual.
- `2`: monitor de Compra Masiva para Piedra Roja, Los Montes -> Escuela Militar, desde el lunes `04-05-2026`, horario `06:54`, asiento `7`, por `7` días.
- `3`: Plan Mayo 2026 ida/vuelta Piedra Roja, con confirmación inicial antes de crear reservas reales.

En el modo `1`, el script preguntará la fecha por consola y luego la regla horaria:
- Hora exacta: acepta uno o más horarios, por ejemplo `06:54` o `06:54, 08:25, 17:54`.
- Ventana horaria: acepta cualquier rango `HH:MM -> HH:MM`, por ejemplo `08:00 -> 10:00`.

La reserva usa los ids del viaje seleccionado para que cualquier horario o franja funcione de forma independiente.
En modo `1`, además del aviso por cambios, el monitor envía un recordatorio por Telegram cada `1800` segundos aunque no haya cambios. Puedes ajustar ese intervalo con `TACERCA_STATUS_NOTIFY_EVERY_SECONDS` (usa `0` para desactivarlo).

El modo `2` envía una alerta a Telegram cuando el viaje gatillo del `04-05-2026` a las `06:54` tiene asientos disponibles. Si los 7 días tienen el mismo horario y el asiento `7` disponible, intenta `trips/create-trip-massive/` con pago según `TACERCA_TYPE_PAYMENT`.

El modo `3` escucha cada `300` segundos y, cuando el viaje gatillo del `04-05-2026` a las `06:54` tiene disponible el asiento `7`, envía `Hay reservas disponibles para Mayo. Se inicia el flujo de reserva masivo` e intenta reservar por bloques de hasta 7 días:
- Ida Los Montes, Piedra Roja -> Escuela Militar a las `06:54`.
- Regreso Escuela Militar -> Los Montes, Piedra Roja a las `19:00`.
- Ida Los Montes, Piedra Roja -> Escuela Militar a las `06:35` para el lunes `11-05-2026`, asiento `7` para el comprador y asiento `8` para el pasajero frecuente `Teodoro Iribarren`.
- Fechas: `04-05-2026`, `05-05-2026`, `06-05-2026`, `07-05-2026`, `08-05-2026`, `12-05-2026`, `13-05-2026`, `14-05-2026`, `15-05-2026`, `18-05-2026`, `19-05-2026`, `20-05-2026`, `22-05-2026`, `26-05-2026`, `27-05-2026`, `28-05-2026`, `29-05-2026`.
- Pago forzado contra billetera (`wallet`).
- Si detecta que un bloque ya fue reservado en Tacerca, envía por Telegram el aviso `bloque ya reservado`.
- Aunque el estado local diga que el plan está completado, vuelve a verificar en Tacerca todos los bloques; si falta una fecha, reserva solo esa fecha faltante.
- Si la verificación confirma que todos los bloques están reservados, envía por Telegram la notificación de plan completado.
- Si un bloque sigue pendiente, envía por Telegram el aviso `bloque pendiente` e indica que se intentará cada `300` segundos reservar los bloques faltantes.
- Si un bloque se reserva durante la ejecución, mantiene la notificación `bloque reservado`.
- Si no hay reservas disponibles, repite por Telegram `No hay reservas disponibles` cada `1800` segundos.
- Estado local: `tacerca_plan_mayo_2026_state.json`.

Variables opcionales para ajustar el modo `2` sin cambiar código:
- `TACERCA_MASSIVE_START_DATE`
- `TACERCA_MASSIVE_HOUR`
- `TACERCA_MASSIVE_SEAT`
- `TACERCA_MASSIVE_DAYS`
- `TACERCA_MASSIVE_POLL_SECONDS`

Variables opcionales para ajustar el modo `3` sin cambiar código:
- `TACERCA_MAY_PLAN_POLL_SECONDS`
- `TACERCA_MAY_PLAN_BLOCK_RETRY_SECONDS`: frecuencia para reintentar un bloque pendiente o fallido. Por defecto `300`.
- `TACERCA_MAY_PLAN_SEAT`
- `TACERCA_MAY_PLAN_CONFIRM`
- `TACERCA_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS`: frecuencia mínima para repetir el aviso "No hay reservas disponibles" mientras el gatillo siga pendiente. Usa `0` para avisar en cada consulta.
