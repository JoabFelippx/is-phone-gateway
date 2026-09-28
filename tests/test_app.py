import struct

import pytest
from fastapi.testclient import TestClient
from is_msgs.common_pb2 import Vector3
from is_msgs.image_pb2 import Image
from is_wire.core import Message
from starlette.websockets import WebSocketDisconnect

from phone_gateway.app import create_app
from tests.test_converters import jpeg

ORIGIN = {"origin": "http://testserver"}


class RecordingPublisher:
    def __init__(self, uri, exchange, *, stream=False):
        self.uri, self.exchange, self.stream = uri, exchange, stream
        self.messages = []
        self.closed = False

    async def connect(self):
        if self.uri.startswith("amqp://offline"):
            raise OSError("secret-password")

    async def heartbeat(self):
        pass

    async def publish(self, content, topic, metadata):
        wire = Message(content=content)
        wire.metadata.update(metadata)
        self.messages.append((wire, topic))
        return len(wire.body)

    async def close(self):
        self.closed = True


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.delenv("BROKER_URI", raising=False)
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    instances = []

    def factory(*args, **kwargs):
        instance = RecordingPublisher(*args, **kwargs)
        instances.append(instance)
        return instance

    with TestClient(create_app(factory)) as client:
        yield client, instances


def configure(ws, **updates):
    config = {
        "broker_uri": "amqp://user:password@broker:5672",
        "device_id": "phone-a",
        "sensors": {
            "camera": {"enabled": True, "topic": "custom.frame"},
            "gyroscope": {"enabled": True, "topic": "custom.gyro", "rate_hz": 10},
        },
    }
    config.update(updates)
    ws.send_json({"type": "configure", "config": config})
    return ws.receive_json()


def test_interface_and_info_do_not_expose_credentials(gateway, monkeypatch):
    client, _ = gateway
    monkeypatch.setenv("BROKER_URI", "amqp://user:secret@broker:5672")
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    info = client.get("/api/info")
    assert info.json()["broker_configured"] is True
    assert "secret" not in info.text


def test_websocket_routes_real_protobuf_and_closes_channels(gateway):
    client, instances = gateway
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        assert configure(ws)["type"] == "ready"
        ws.send_json(
            {
                "sensor": "gyroscope",
                "timestamp_ms": 1700000000123,
                "data": {"alpha": 180, "beta": 0, "gamma": 0},
            }
        )
        assert ws.receive_json()["topic"] == "custom.gyro"
        ws.send_bytes(struct.pack("!Q", 1700000000123) + jpeg())
        ack = ws.receive_json()
        assert ack["sensor"] == "camera" and ack["count"] == 1
    assert len(instances) == 2
    assert all(instance.closed for instance in instances)
    camera = next(instance for instance in instances if instance.stream)
    telemetry = next(instance for instance in instances if not instance.stream)
    message, topic = camera.messages[0]
    assert topic == "custom.frame" and message.unpack(Image).data == jpeg()
    assert message.metadata["observed_at_ms"] == "1700000000123"
    assert telemetry.messages[0][0].unpack(Vector3).z > 3


def test_sessions_do_not_share_topics_or_device_ids(gateway):
    client, instances = gateway
    with client.websocket_connect("/ws", headers=ORIGIN) as first:
        assert configure(first)["type"] == "ready"
        with client.websocket_connect("/ws", headers=ORIGIN) as second:
            assert (
                configure(
                    second,
                    device_id="phone-b",
                    sensors={"camera": {"enabled": True, "topic": "other.frame"}},
                )["type"]
                == "ready"
            )
            second.send_bytes(struct.pack("!Q", 1700000000123) + jpeg())
            assert second.receive_json()["topic"] == "other.frame"
            first.send_bytes(struct.pack("!Q", 1700000000123) + jpeg())
            assert first.receive_json()["topic"] == "custom.frame"
    ids = {
        instance.messages[0][0].metadata["device_id"] for instance in instances if instance.messages
    }
    assert ids == {"phone-a", "phone-b"}


def test_disabled_sensor_invalid_payload_and_rate_limit(gateway):
    client, instances = gateway
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        assert configure(ws)["type"] == "ready"
        ws.send_json({"sensor": "gps", "timestamp_ms": 1, "data": {}})
        assert ws.receive_json()["type"] == "error"
        ws.send_bytes(b"invalid")
        assert ws.receive_json()["type"] == "error"
        for expected in ("published", "skipped"):
            ws.send_json(
                {
                    "sensor": "gyroscope",
                    "timestamp_ms": 1,
                    "data": {"alpha": 1, "beta": 1, "gamma": 1},
                }
            )
            assert ws.receive_json()["type"] == expected
    assert sum(len(instance.messages) for instance in instances) == 1


def test_token_required_and_cross_origin_rejected(gateway, monkeypatch):
    client, instances = gateway
    monkeypatch.setenv("GATEWAY_TOKEN", "private")
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        assert configure(ws)["message"] == "Token de acesso inválido."
    assert not instances
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass


def test_broker_error_does_not_expose_exception_credentials(gateway):
    client, instances = gateway
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        result = configure(ws, broker_uri="amqp://offline:secret-password@broker")
        assert result["type"] == "error"
        assert "secret-password" not in str(result)
    assert all(instance.closed for instance in instances)


def test_configured_environment_broker_and_valid_token(gateway, monkeypatch):
    client, instances = gateway
    monkeypatch.setenv("GATEWAY_TOKEN", "private")
    monkeypatch.setenv("BROKER_URI", "amqp://server:secret@broker")
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(
            {
                "type": "configure",
                "token": "private",
                "config": {
                    "broker_uri": "",
                    "sensors": {"camera": {"enabled": True, "topic": "phone.frame"}},
                },
            }
        )
        assert ws.receive_json()["type"] == "ready"
    assert instances[0].uri == "amqp://server:secret@broker"
