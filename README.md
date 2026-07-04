# cafelab-mqtt-bridge

Consumer MQTT local para CafeLab. Se suscribe a `cafelab/iot/telemetry` y reenvia cada lectura al API Gateway:

```text
MQTT -> POST /api/v1/telemetry-records
```

## Uso rapido

```powershell
cd C:\dev\FUNDAMENTOS\mqtt-bridge
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
python .\mqtt_bridge.py
```

## Autenticacion

En `.env`, pega el JWT en `AUTH_TOKEN` sin la palabra `Bearer`.

Deja `X_USER_ID` vacio cuando publiques por el API Gateway. El gateway calcula e inyecta `X-User-Id` desde el Bearer; si el bridge lo manda manualmente puede provocar errores como el `500` que viste en Postman.
