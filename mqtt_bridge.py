import getpass
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cafelab.mqtt_bridge")


@dataclass(frozen=True)
class BridgeConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_topic: str
    mqtt_client_id: str
    api_gateway_base_url: str
    telemetry_endpoint: str
    auth_endpoint: str
    auth_email: str
    auth_password: str
    auth_token: str
    auth_prompt: bool
    x_user_id: str
    http_timeout_seconds: float
    http_max_retries: int

    @property
    def telemetry_url(self) -> str:
        return f"{self.api_gateway_base_url.rstrip('/')}{self.telemetry_endpoint}"

    @property
    def auth_url(self) -> str:
        return f"{self.api_gateway_base_url.rstrip('/')}{self.auth_endpoint}"

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            mqtt_host=os.environ.get("MQTT_BROKER_HOST", "localhost"),
            mqtt_port=int(os.environ.get("MQTT_BROKER_PORT", "1883")),
            mqtt_topic=os.environ.get("MQTT_TOPIC", "cafelab/iot/telemetry"),
            mqtt_client_id=os.environ.get("MQTT_CLIENT_ID", "cafelab-mqtt-bridge-local"),
            api_gateway_base_url=os.environ.get(
                "API_GATEWAY_BASE_URL",
                "https://cafelab-api-gateway-gnfua0csgsbud3eh.canadacentral-01.azurewebsites.net",
            ),
            telemetry_endpoint=os.environ.get("TELEMETRY_ENDPOINT", "/api/v1/telemetry-records"),
            auth_endpoint=os.environ.get("AUTH_ENDPOINT", "/api/v1/authentication/sign-in"),
            auth_email=os.environ.get("AUTH_EMAIL", "").strip(),
            auth_password=os.environ.get("AUTH_PASSWORD", "").strip(),
            auth_token=os.environ.get("AUTH_TOKEN", "").strip(),
            auth_prompt=os.environ.get("AUTH_PROMPT", "true").strip().lower() not in {"0", "false", "no"},
            x_user_id=os.environ.get("X_USER_ID", "").strip(),
            http_timeout_seconds=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "10")),
            http_max_retries=int(os.environ.get("HTTP_MAX_RETRIES", "3")),
        )


REQUIRED_FIELDS = ("coffeeLotId", "temperature", "humidity", "timestamp")


def to_backend_timestamp(value: Any) -> str:
    """Convert MQTT ISO timestamps to the LocalDateTime shape expected by IoT Monitoring."""
    raw_value = str(value).strip()
    if not raw_value:
        raise ValueError("timestamp cannot be empty")

    normalized = raw_value.replace("Z", "+00:00") if raw_value.endswith("Z") else raw_value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"timestamp must be ISO-8601 compatible: {raw_value}") from error

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed.isoformat(timespec="milliseconds")


def normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"Missing required telemetry field(s): {', '.join(missing)}")

    return {
        "coffeeLotId": int(raw["coffeeLotId"]),
        "temperature": float(raw["temperature"]),
        "humidity": float(raw["humidity"]),
        "timestamp": to_backend_timestamp(raw["timestamp"]),
    }


class TelemetryBridge:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.session = requests.Session()
        self._auth_email = config.auth_email
        self._auth_password = config.auth_password
        self._auth_token = config.auth_token or None

    def has_login_credentials(self) -> bool:
        return bool(self._auth_email and self._auth_password)

    def ensure_login_credentials(self) -> None:
        if self.has_login_credentials() or not self.config.auth_prompt:
            return
        logger.info("CafeLab credentials were not found in environment variables.")
        self._auth_email = input("CafeLab email: ").strip()
        self._auth_password = getpass.getpass("CafeLab password: ").strip()

    def authenticate(self, force: bool = False) -> str:
        if self._auth_token and not force:
            return self._auth_token
        self.ensure_login_credentials()
        if not self.has_login_credentials():
            raise RuntimeError(
                "No AUTH_TOKEN was provided and CafeLab credentials are incomplete."
            )

        logger.info("Signing in to CafeLab IAM as %s", self._auth_email)
        response = self.session.post(
            self.config.auth_url,
            json={
                "email": self._auth_email,
                "password": self._auth_password,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.config.http_timeout_seconds,
        )
        logger.info("IAM sign-in HTTP %s", response.status_code)
        if response.status_code >= 400:
            raise RuntimeError(f"IAM sign-in failed: HTTP {response.status_code} {response.text}")

        try:
            body = response.json()
        except ValueError as error:
            raise RuntimeError("IAM sign-in response is not valid JSON") from error

        token = body.get("token") or body.get("jwt") or body.get("accessToken")
        if not token:
            raise RuntimeError("IAM sign-in response did not include a token")

        self._auth_token = str(token)
        logger.info("IAM sign-in succeeded for user id=%s email=%s", body.get("id"), body.get("email"))
        return self._auth_token

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.config.x_user_id:
            headers["X-User-Id"] = self.config.x_user_id
        headers["Authorization"] = f"Bearer {self.authenticate()}"
        return headers

    def post_to_gateway(self, payload: dict[str, Any]) -> None:
        logger.info("Body to API Gateway: %s", payload)
        last_error: Exception | None = None

        for attempt in range(1, self.config.http_max_retries + 1):
            try:
                response = self.session.post(
                    self.config.telemetry_url,
                    json=payload,
                    headers=self.headers(),
                    timeout=self.config.http_timeout_seconds,
                )
            except requests.RequestException as error:
                last_error = error
                logger.warning("HTTP request failed on attempt %s: %s", attempt, error)
                self._sleep_before_retry(attempt)
                continue

            logger.info("API Gateway HTTP %s", response.status_code)
            logger.info("API Gateway response: %s", response.text)

            if 200 <= response.status_code < 300:
                return
            if response.status_code == 401 and self.has_login_credentials():
                logger.warning("Bearer was rejected; refreshing JWT before retrying")
                self._auth_token = None
                try:
                    self.authenticate(force=True)
                except RuntimeError as error:
                    logger.error("Unable to refresh JWT: %s", error)
                    return
                self._sleep_before_retry(attempt)
                continue
            if 400 <= response.status_code < 500:
                logger.error("Permanent 4xx error. Message will be skipped.")
                return

            self._sleep_before_retry(attempt)

        if last_error is not None:
            logger.error("Message failed after retries: %s", last_error)
        else:
            logger.error("Message failed after %s retries", self.config.http_max_retries)

    def _sleep_before_retry(self, attempt: int) -> None:
        if attempt < self.config.http_max_retries:
            time.sleep(min(2 ** (attempt - 1), 5))

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if int(reason_code) != 0:
            logger.error("MQTT connection failed with reason_code=%s", reason_code)
            return
        logger.info("Connected to MQTT broker %s:%s", self.config.mqtt_host, self.config.mqtt_port)
        client.subscribe(self.config.mqtt_topic, qos=1)
        logger.info("Subscribed to MQTT topic %s", self.config.mqtt_topic)

    def on_message(self, client, userdata, message):
        text = message.payload.decode("utf-8", errors="replace")
        logger.info("MQTT message received from %s: %s", message.topic, text)
        try:
            raw = json.loads(text)
            payload = normalize_payload(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as error:
            logger.error("Invalid MQTT payload. Message skipped: %s", error)
            return

        self.post_to_gateway(payload)

    def run(self) -> None:
        logger.info("Starting CafeLab MQTT bridge")
        logger.info("API Gateway URL: %s", self.config.telemetry_url)
        logger.info("MQTT topic: %s", self.config.mqtt_topic)
        if self.config.auth_token:
            logger.info("Using AUTH_TOKEN from environment")
        elif self.has_login_credentials() or self.config.auth_prompt:
            self.authenticate()
        else:
            raise SystemExit(
                "Authentication is required. Set AUTH_EMAIL and AUTH_PASSWORD as environment variables, "
                "enable AUTH_PROMPT, or provide AUTH_TOKEN for a short demo."
            )

        client = mqtt.Client(client_id=self.config.mqtt_client_id, clean_session=True)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
        client.loop_forever()


if __name__ == "__main__":
    TelemetryBridge(BridgeConfig.from_env()).run()


