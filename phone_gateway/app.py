import asyncio
import contextlib
import json
import os
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from phone_gateway.broker import BrokerPublisher
from phone_gateway.converters import MAX_FRAME_BYTES, convert_frame, convert_sample
from phone_gateway.models import SENSORS, GatewayConfig, Sample

STATIC = Path(__file__).parent / "static"


def create_app(publisher_factory=BrokerPublisher):
    app = FastAPI(title="Phone Gateway", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    async def info():
        return {
            "sensors": SENSORS,
            "broker_configured": bool(os.getenv("BROKER_URI")),
            "token_required": bool(os.getenv("GATEWAY_TOKEN")),
            "version": "0.1.0",
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        origin = urlsplit(ws.headers.get("origin", ""))
        if origin.scheme not in {"https", "http"} or origin.netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return
        await ws.accept()
        publishers = {}
        heartbeat_task = None
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=30)
            if len(raw) > 16384:
                raise ValueError("Configuração muito grande.")
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("type") != "configure":
                raise ValueError("A primeira mensagem deve configurar o gateway.")
            expected_token = os.getenv("GATEWAY_TOKEN", "")
            token = request.get("token", "")
            if expected_token and (
                not isinstance(token, str)
                or not secrets.compare_digest(token.encode(), expected_token.encode())
            ):
                await ws.send_json({"type": "error", "message": "Token de acesso inválido."})
                await ws.close(code=1008)
                return
            config = GatewayConfig.model_validate(request.get("config"))
            uri = config.broker_uri or os.getenv("BROKER_URI", "")
            if not uri:
                raise ValueError("Informe o endereço do broker.")
            # Valide também o endereço vindo do ambiente, sem expor credenciais em erros.
            GatewayConfig.validate_uri(uri)
            groups = {
                "camera" if name == "camera" else "telemetry"
                for name, sensor in config.sensors.items()
                if sensor.enabled
            }
            for group in groups:
                publisher = publisher_factory(uri, config.exchange, stream=group == "camera")
                publishers[group] = publisher
                try:
                    await publisher.connect()
                except Exception:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": (
                                "Não foi possível conectar ao broker. "
                                "Confira endereço, porta, usuário e senha."
                            ),
                        }
                    )
                    await ws.close(code=1011)
                    return
            await ws.send_json({"type": "ready", "device_id": config.device_id})

            async def heartbeats():
                while True:
                    await asyncio.sleep(5)
                    try:
                        await asyncio.gather(
                            *(publisher.heartbeat() for publisher in publishers.values())
                        )
                    except Exception:
                        await ws.close(code=1011, reason="Conexão com o broker interrompida.")
                        return

            heartbeat_task = asyncio.create_task(heartbeats())
            counts = {}
            last_sent = {}
            while True:
                packet = await ws.receive()
                if packet["type"] == "websocket.disconnect":
                    break
                sensor_name = None
                try:
                    if packet.get("bytes") is not None:
                        binary = packet["bytes"]
                        sensor_name = "camera"
                        if not 8 < len(binary) <= MAX_FRAME_BYTES + 8:
                            raise ValueError("Tamanho do quadro inválido.")
                        timestamp = struct.unpack("!Q", binary[:8])[0]
                        if timestamp > 253402300799999:
                            raise ValueError("Timestamp inválido.")
                        sample = None
                    else:
                        raw = packet.get("text", "")
                        if len(raw) > 16384:
                            raise ValueError("Amostra muito grande.")
                        sample = Sample.model_validate_json(raw)
                        sensor_name, timestamp = sample.sensor, sample.timestamp_ms
                    setting = config.sensors.get(sensor_name)
                    if not setting or not setting.enabled:
                        raise ValueError("Sensor não habilitado nesta sessão.")
                    current = time.monotonic()
                    if current - last_sent.get(sensor_name, -float("inf")) < 0.95 / setting.rate_hz:
                        await ws.send_json({"type": "skipped", "sensor": sensor_name})
                        continue
                    sequence = counts.get(sensor_name, 0) + 1
                    content = (
                        convert_frame(binary[8:], config.device_id, sequence)
                        if sample is None
                        else convert_sample(sample)
                    )
                    metadata = {
                        "device_id": config.device_id,
                        "sensor": sensor_name,
                        "observed_at_ms": str(timestamp),
                        "schema": content.DESCRIPTOR.full_name,
                        "sequence": str(sequence),
                    }
                    if sensor_name == "accelerometer":
                        metadata.update(unit="m/s^2", includes_gravity="false")
                    if sensor_name == "gyroscope":
                        metadata.update(unit="rad/s", axes="x=beta,y=gamma,z=alpha")
                    group = "camera" if sensor_name == "camera" else "telemetry"
                    try:
                        size = await publishers[group].publish(content, setting.topic, metadata)
                    except Exception:
                        await ws.send_json(
                            {
                                "type": "error",
                                "sensor": sensor_name,
                                "message": (
                                    "Publicação interrompida. "
                                    "Verifique o broker e inicie uma nova sessão."
                                ),
                            }
                        )
                        await ws.close(code=1011)
                        break
                    counts[sensor_name] = sequence
                    last_sent[sensor_name] = current
                    await ws.send_json(
                        {
                            "type": "published",
                            "sensor": sensor_name,
                            "topic": setting.topic,
                            "count": sequence,
                            "bytes": size,
                        }
                    )
                except (ValidationError, ValueError, json.JSONDecodeError):
                    await ws.send_json(
                        {
                            "type": "error",
                            "sensor": sensor_name,
                            "message": "Amostra inválida ou sensor não habilitado.",
                        }
                    )
        except WebSocketDisconnect:
            pass
        except (ValidationError, ValueError, json.JSONDecodeError, asyncio.TimeoutError):
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await ws.send_json(
                    {
                        "type": "error",
                        "message": (
                            "Configuração inválida. "
                            "Confira broker, tópicos distintos e taxas dos sensores."
                        ),
                    }
                )
                await ws.close(code=1008)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            for publisher in publishers.values():
                with contextlib.suppress(Exception):
                    await publisher.close()

    return app


app = create_app()
