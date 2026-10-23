# Monitor web configurable Tacerca

La aplicación `monitor_web.py` mantiene una interfaz web y un worker en el mismo
proceso. La configuración y los resultados se guardan en SQLite (`data/monitor.db`),
por lo que sobreviven reinicios.

## Funciones

- Uno o varios días, escritos como `AAAA-MM-DD` o `DD-MM-AAAA`.
- Una o varias horas exactas, o un rango horario (también cruza medianoche).
- Ida, vuelta o ambos trayectos.
- Entre 1 y 8 asientos. Los asientos adicionales se asignan, en orden, a los
  pasajeros frecuentes del perfil Tacerca.
- Compra exclusivamente con `wallet`, previa comprobación de saldo y límite de precio.
- Un resultado de pago incierto queda en estado `review`: no se cobra otra vez
  automáticamente.
- Autenticación HTTP Basic, token CSRF y notificaciones por Telegram.

## Ejecución local

Completa `.env` a partir de `.env.example` y define, como mínimo:

```dotenv
TACERCA_USERNAME=usuario
TACERCA_PASSWORD=clave-tacerca
TACERCA_CUSTOMER_FIRST_NAME=Nombre
TACERCA_CUSTOMER_LAST_NAME=Apellido
TACERCA_CUSTOMER_EMAIL=correo@example.com
TACERCA_CUSTOMER_PHONE=telefono
TACERCA_CUSTOMER_RUT=rut
TACERCA_WEB_USERNAME=admin
TACERCA_WEB_PASSWORD=una-clave-larga
TACERCA_WEB_SECRET_KEY=otra-clave-larga-y-aleatoria
```

Luego:

```bash
python3 -m pip install -r requirements.txt
gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8080 monitor_web:app
```

Se usa un solo worker de Gunicorn para garantizar que exista un único ciclo de
monitoreo. Abre `http://localhost:8080` e ingresa las credenciales web.

## Despliegue continuo en la VM de GCP

El workflow manual `Deploy configurable web monitor` reutiliza la VM configurada
en el repositorio e instala `deploy/tacerca-web.service`. El servicio se reinicia
automáticamente y queda habilitado después de reiniciar la VM.

Configura estos secretos en GitHub Actions:

- Existentes: `GCP_VM_HOST`, `GCP_VM_SSH_KEY`, `GCP_VM_USER`, `GCP_PROJECT_PATH`.
- Nuevos: `TACERCA_WEB_USERNAME`, `TACERCA_WEB_PASSWORD`,
  `TACERCA_WEB_SECRET_KEY`.

La VM debe conservar las credenciales Tacerca y Telegram en `.env`. También debe
permitir tráfico al puerto 8080 o publicar el servicio mediante un proxy HTTPS.
La gratuidad depende de que la VM, región y consumo permanezcan dentro del nivel
gratuito de la cuenta de GCP.

## Operación

```bash
sudo systemctl status tacerca-web
sudo journalctl -u tacerca-web -f
```

Estados de reserva: `pending`, `waiting`, `submitting`, `booked`, `review` y
`expired`. Un estado `review` requiere comprobar Tacerca manualmente antes de
crear otro monitor para el mismo viaje.
