import math
import time
from io import BytesIO

from is_msgs.common_pb2 import Vector3
from is_msgs.image_pb2 import TIMESTAMP_SOURCE_GATEWAY_RECEIVE, Image
from is_msgs.power_pb2 import PowerInfo
from is_msgs.ros_pb2 import ROSMessage
from PIL import Image as PillowImage
from PIL import UnidentifiedImageError

from phone_gateway.models import Sample

MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_FRAME_PIXELS = 1920 * 1080


def number(data, key, *, optional=False):
    value = data.get(key)
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key}: valor numérico finito obrigatório.")
    return value


def vector(data):
    return Vector3(**{axis: number(data, axis) for axis in ("x", "y", "z")})


def convert_sample(sample: Sample):
    data = sample.data
    if sample.sensor == "accelerometer":
        # acceleration (sem gravidade) é a única fonte aceita nesta versão.
        return vector(data)
    if sample.sensor == "gyroscope":
        # DeviceMotion.rotationRate é em graus/s: beta→x, gamma→y, alpha→z.
        return Vector3(
            x=math.radians(number(data, "beta")),
            y=math.radians(number(data, "gamma")),
            z=math.radians(number(data, "alpha")),
        )
    if sample.sensor == "orientation":
        if not isinstance(data.get("absolute"), bool):
            raise ValueError("absolute: valor booleano obrigatório.")
        content = {axis: math.radians(number(data, axis)) for axis in ("alpha", "beta", "gamma")}
        content.update(absolute=data["absolute"], rotation_order="Z-X'-Y''", unit="rad")
        result = ROSMessage(type="phone_gateway/DeviceOrientation")
        result.content.update(content)
        return result
    if sample.sensor == "gps":
        latitude, longitude = number(data, "latitude"), number(data, "longitude")
        accuracy = number(data, "accuracy")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or accuracy < 0:
            raise ValueError("Coordenadas ou precisão de localização inválidas.")
        # A altitude do navegador é relativa ao nível do mar, não ao elipsoide WGS84.
        # Preserve a semântica em vez de rotular incorretamente como ROS NavSatFix.
        content = {"latitude": latitude, "longitude": longitude, "accuracy_m": accuracy}
        for source, target in (
            ("altitude", "altitude_m"),
            ("altitudeAccuracy", "altitude_accuracy_m"),
            ("heading", "heading_deg"),
            ("speed", "speed_m_s"),
        ):
            value = number(data, source, optional=True)
            if value is not None:
                if source in {"altitudeAccuracy", "speed"} and value < 0:
                    raise ValueError(f"{source}: valor negativo inválido.")
                if source == "heading" and not 0 <= value < 360:
                    raise ValueError("heading: esperado um ângulo entre 0 e 360 graus.")
                content[target] = value
        result = ROSMessage(type="phone_gateway/Geolocation")
        result.content.update(content)
        return result
    if sample.sensor == "battery":
        level = number(data, "level")
        charging = data.get("charging")
        if not 0 <= level <= 1 or not isinstance(charging, bool):
            raise ValueError("Bateria: level entre 0 e 1 e charging booleano são obrigatórios.")
        status = (
            PowerInfo.CHARGED
            if level == 1 and charging
            else (PowerInfo.CHARGING if charging else PowerInfo.DISCHARGING)
        )
        result = PowerInfo(charge=level, status=status)
        autonomy = number(data, "dischargingTime", optional=True)
        if not charging and autonomy is not None and 0 <= autonomy < 315576000000:
            result.autonomy.FromMilliseconds(int(autonomy * 1000))
        return result
    raise ValueError("Envie a câmera pelo canal binário.")


def convert_frame(jpeg: bytes, device_id: str, sequence: int):
    if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
        raise ValueError("Quadro vazio ou maior que 2 MiB.")
    try:
        with PillowImage.open(BytesIO(jpeg)) as image:
            if image.format != "JPEG" or image.width * image.height > MAX_FRAME_PIXELS:
                raise ValueError("Esperado JPEG com até 1920 × 1080 pixels.")
            image.verify()
    except (UnidentifiedImageError, OSError, PillowImage.DecompressionBombError) as error:
        raise ValueError("Imagem JPEG inválida.") from error
    result = Image(data=jpeg, sequence=sequence, timestamp_source=TIMESTAMP_SOURCE_GATEWAY_RECEIVE)
    result.header.frame_id = f"{device_id}/camera"
    result.header.stamp.FromNanoseconds(time.time_ns())
    return result
