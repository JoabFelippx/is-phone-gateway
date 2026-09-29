from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_BROKER_URI = "amqp://guest:guest@10.10.50.176:30000"

SENSORS = {
    "camera": {
        "label": "Câmera",
        "topic": "cameraphonegateway.frame",
        "schema": "is.vision.Image",
        "unit": "JPEG",
        "rate": 2,
        "max_rate": 30,
    },
    "accelerometer": {
        "label": "Acelerômetro",
        "topic": "phonegateway.acceleration",
        "schema": "is.common.Vector3",
        "unit": "m/s²",
        "rate": 10,
        "max_rate": 30,
    },
    "gyroscope": {
        "label": "Giroscópio",
        "topic": "phonegateway.angular_velocity",
        "schema": "is.common.Vector3",
        "unit": "rad/s",
        "rate": 10,
        "max_rate": 30,
    },
    "orientation": {
        "label": "Orientação",
        "topic": "phonegateway.orientation",
        "schema": "is.ros.ROSMessage",
        "unit": "rad",
        "rate": 10,
        "max_rate": 30,
    },
    "gps": {
        "label": "Localização",
        "topic": "phonegateway.location",
        "schema": "is.ros.ROSMessage",
        "unit": "graus / m",
        "rate": 1,
        "max_rate": 5,
    },
    "battery": {
        "label": "Bateria",
        "topic": "phonegateway.battery",
        "schema": "is.common.PowerInfo",
        "unit": "%",
        "rate": 0.2,
        "max_rate": 1,
    },
}
SensorName = Literal["camera", "accelerometer", "gyroscope", "orientation", "gps", "battery"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SensorConfig(StrictModel):
    enabled: bool = False
    topic: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"
    )
    rate_hz: float = Field(default=1, ge=0.05, le=30)


class GatewayConfig(StrictModel):
    broker_uri: str = Field(default="", max_length=2048)
    exchange: str = Field(default="is", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    device_id: str = Field(
        default="phone", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    sensors: dict[SensorName, SensorConfig]

    @field_validator("broker_uri")
    @classmethod
    def validate_uri(cls, value):
        if not value:
            return value
        url = urlsplit(value)
        if url.scheme not in {"amqp", "amqps"} or not url.hostname:
            raise ValueError("Use uma URI amqp:// ou amqps:// com endereço do broker.")
        if url.query or url.fragment:
            raise ValueError("A URI do broker não aceita query ou fragmento.")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("Porta inválida.")
        return value

    @model_validator(mode="after")
    def validate_sensors(self):
        active = [sensor for sensor in self.sensors.values() if sensor.enabled]
        if not active:
            raise ValueError("Selecione pelo menos um sensor.")
        if len({sensor.topic for sensor in active}) != len(active):
            raise ValueError("Use um tópico diferente para cada sensor ativo.")
        for name, sensor in self.sensors.items():
            if sensor.rate_hz > SENSORS[name]["max_rate"]:
                unit = "FPS" if name == "camera" else "Hz"
                raise ValueError(f"Taxa máxima de {name}: {SENSORS[name]['max_rate']} {unit}.")
        return self


class Sample(StrictModel):
    sensor: SensorName
    timestamp_ms: int = Field(ge=0, le=253402300799999)
    data: dict
