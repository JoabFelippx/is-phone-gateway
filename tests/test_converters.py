import math
from io import BytesIO

import pytest
from is_msgs.common_pb2 import Vector3
from is_msgs.image_pb2 import TIMESTAMP_SOURCE_GATEWAY_RECEIVE, Image
from is_msgs.power_pb2 import PowerInfo
from is_msgs.ros_pb2 import ROSMessage
from is_wire.core import Message
from PIL import Image as PillowImage

from phone_gateway.converters import convert_frame, convert_sample
from phone_gateway.models import GatewayConfig, Sample


def sample(sensor, data):
    return Sample(sensor=sensor, data=data, timestamp_ms=1700000000123)


def jpeg():
    buffer = BytesIO()
    PillowImage.new("RGB", (16, 12), (35, 180, 120)).save(buffer, "JPEG")
    return buffer.getvalue()


def test_gyroscope_axes_and_units_survive_wire_roundtrip():
    result = convert_sample(sample("gyroscope", {"alpha": 180, "beta": 90, "gamma": -45}))
    decoded = Message(content=result).unpack(Vector3)
    assert decoded.x == pytest.approx(math.pi / 2)
    assert decoded.y == pytest.approx(-math.pi / 4)
    assert decoded.z == pytest.approx(math.pi)


def test_acceleration_does_not_substitute_missing_axes_or_gravity():
    result = convert_sample(sample("accelerometer", {"x": 0, "y": -2.5, "z": 1}))
    assert result.y == -2.5
    for data in (
        {"x": None, "y": 1, "z": 1},
        {"x": float("nan"), "y": 1, "z": 1},
        {"x": True, "y": 1, "z": 1},
        {"x": "1", "y": 1, "z": 1},
    ):
        with pytest.raises(ValueError):
            convert_sample(sample("accelerometer", data))


def test_orientation_preserves_reference_and_rotation_order():
    result = convert_sample(
        sample("orientation", {"alpha": 90, "beta": 0, "gamma": -30, "absolute": False})
    )
    decoded = Message(content=result).unpack(ROSMessage)
    assert decoded.type == "phone_gateway/DeviceOrientation"
    assert decoded.content["alpha"] == pytest.approx(math.pi / 2)
    assert decoded.content["absolute"] is False
    assert decoded.content["rotation_order"] == "Z-X'-Y''"


def test_gps_keeps_precision_and_does_not_invent_altitude():
    data = {
        "latitude": -20.123456789,
        "longitude": -40.987654321,
        "accuracy": 5,
        "altitude": None,
        "heading": None,
        "speed": 0,
    }
    result = convert_sample(sample("gps", data))
    decoded = Message(content=result).unpack(ROSMessage)
    assert decoded.content["latitude"] == data["latitude"]
    assert "altitude_m" not in decoded.content
    assert decoded.content["speed_m_s"] == 0
    with pytest.raises(ValueError):
        convert_sample(sample("gps", {**data, "latitude": 100}))


def test_battery_charge_and_unknown_fields():
    result = convert_sample(
        sample("battery", {"level": 0.75, "charging": False, "dischargingTime": None})
    )
    decoded = Message(content=result).unpack(PowerInfo)
    assert decoded.charge == 0.75
    assert decoded.status == PowerInfo.DISCHARGING
    assert not decoded.HasField("autonomy")
    result = convert_sample(sample("battery", {"level": 1, "charging": True}))
    assert result.status == PowerInfo.CHARGED


def test_jpeg_is_binary_image_with_truthful_receive_timestamp():
    data = jpeg()
    result = convert_frame(data, "phone-a", 42)
    decoded = Message(content=result).unpack(Image)
    assert decoded.data == data
    assert decoded.sequence == 42
    assert decoded.header.frame_id == "phone-a/camera"
    assert decoded.header.stamp.seconds > 0
    assert decoded.timestamp_source == TIMESTAMP_SOURCE_GATEWAY_RECEIVE
    with pytest.raises(ValueError):
        convert_frame(b"not a jpeg", "phone", 1)


@pytest.mark.parametrize(
    "uri", ["http://broker", "amqp://", "amqp://broker:99999", "amqp://broker?vhost=x"]
)
def test_broker_uri_validation(uri):
    with pytest.raises(ValueError):
        GatewayConfig(broker_uri=uri, sensors={"gps": {"enabled": True, "topic": "phone.gps"}})


def test_duplicate_topics_and_excessive_frequency_are_rejected():
    with pytest.raises(ValueError):
        GatewayConfig(
            sensors={
                "gps": {"enabled": True, "topic": "same.topic"},
                "camera": {"enabled": True, "topic": "same.topic"},
            }
        )
    config = GatewayConfig(
        sensors={"camera": {"enabled": True, "topic": "phone.frame", "rate_hz": 30}}
    )
    assert config.sensors["camera"].rate_hz == 30
    with pytest.raises(ValueError):
        GatewayConfig(sensors={"camera": {"enabled": True, "topic": "phone.frame", "rate_hz": 31}})
