# CafeLab MQTT Bridge

Servicio Python que consume lecturas del broker MQTT local y las reenvia al API Gateway de CafeLab.

```text
Mosquitto topic cafelab/iot/telemetry -> mqtt-bridge -> POST /api/v1/telemetry-records
```

El bridge inicia sesion en IAM, obtiene el JWT y lo usa como Bearer para publicar telemetria. Para uso local no necesitas escribir correo, contrasena ni token en `.env`: con `AUTH_PROMPT=true`, el bridge pide el correo y la contrasena por consola al iniciar y no los guarda.

Si el API Gateway responde `401`, intenta refrescar el token y reintenta.

## Requisitos

- Python 3.10 o superior.
- Broker Mosquitto levantado en `localhost:1883`.
- Cuenta CafeLab con acceso al lote que recibira lecturas.

## Instalacion local

```powershell
cd C:\dev\FUNDAMENTOS\mqtt-bridge
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

El `.env` local debe quedarse sin secretos:

```env
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_TOPIC=cafelab/iot/telemetry
MQTT_CLIENT_ID=cafelab-mqtt-bridge-local
API_GATEWAY_BASE_URL=https://cafelab-api-gateway-gnfua0csgsbud3eh.canadacentral-01.azurewebsites.net
TELEMETRY_ENDPOINT=/api/v1/telemetry-records
AUTH_ENDPOINT=/api/v1/authentication/sign-in
AUTH_PROMPT=true
AUTH_EMAIL=
AUTH_PASSWORD=
AUTH_TOKEN=
X_USER_ID=
HTTP_TIMEOUT_SECONDS=10
HTTP_MAX_RETRIES=3
```

Con `AUTH_PROMPT=true`, al iniciar veras:

```text
CafeLab email:
CafeLab password:
```

La contrasena no se muestra ni se guarda.

Para despliegue automatizado, no uses `.env` con secretos versionados. Configura `AUTH_EMAIL` y `AUTH_PASSWORD` como variables de entorno del servicio o contenedor.

No llenes `X_USER_ID` cuando uses el API Gateway. El gateway deriva e inyecta ese header desde el Bearer.

`AUTH_TOKEN` es opcional y solo sirve como override temporal para demos cortas. No lo subas a GitHub.

## Levantar

```powershell
cd C:\dev\FUNDAMENTOS\mqtt-bridge
.\.venv\Scripts\activate
python .\mqtt_bridge.py
```

Logs esperados:

```text
Starting CafeLab MQTT bridge
CafeLab credentials were not found in environment variables.
CafeLab email:
CafeLab password:
Signing in to CafeLab IAM as ...
IAM sign-in succeeded ...
Connected to MQTT broker localhost:1883
Subscribed to MQTT topic cafelab/iot/telemetry
API Gateway HTTP 201
```

## Probar con un mensaje manual

Con el broker levantado:

```powershell
docker exec -it cafelab-mosquitto mosquitto_pub -h localhost -p 1883 -t cafelab/iot/telemetry -m '{"coffeeLotId":32,"temperature":25.5,"humidity":60.2,"timestamp":"2026-07-04T18:47:11"}'
```

El bridge debe registrar el mensaje y responder con HTTP `201` desde el API Gateway.

## Apagar

Detener bridge:

```powershell
Ctrl+C
```

Apagar broker:

```powershell
cd C:\dev\FUNDAMENTOS\mqtt-broker
docker compose down
```

## Payload esperado

```json
{
  "coffeeLotId": 32,
  "temperature": 25.5,
  "humidity": 60.2,
  "timestamp": "2026-07-04T18:47:11"
}
```

El bridge ignora campos adicionales y reenvia solo los campos esperados por `POST /api/v1/telemetry-records`. Si el timestamp llega con `Z` u offset, lo convierte al formato `LocalDateTime` que espera el backend IoT.

## Seguridad

- No subas `.env` a GitHub.
- No subas tokens reales.
- No escribas correo ni contrasena en `.env` para uso local; usa el prompt interactivo.
- Para despliegue, configura secretos como variables de entorno seguras del ambiente.
