"""Smoke test opcional da interface com câmera e sensores simulados no Chrome.

Instale playwright no ambiente de desenvolvimento antes de executar.
"""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from io import BytesIO

from PIL import Image as PillowImage
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser",
        default=shutil.which("google-chrome"),
        help="Caminho do Chrome; sem ele, usa o Chromium do Playwright",
    )
    args = parser.parse_args()
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "phone_gateway.cli", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    address = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(address + "/health", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("O servidor de teste não iniciou.")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=args.browser,
                headless=True,
                args=[
                    "--no-sandbox",
                    "--use-fake-device-for-media-stream",
                    "--use-fake-ui-for-media-stream",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 1000},
                permissions=["camera", "geolocation"],
                geolocation={"latitude": -20.3, "longitude": -40.3},
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                navigator.getBattery = async () => Object.assign(new EventTarget(),
                    {level: 0.75, charging: false, dischargingTime: Infinity});
                """)
            packets = []
            counts = {}
            camera_sizes = []
            padded_frames = []

            def intercept(ws):
                def receive(payload):
                    if isinstance(payload, bytes):
                        assert payload[8:10] == b"\xff\xd8"
                        with PillowImage.open(BytesIO(payload[8:])) as image:
                            camera_sizes.append(image.size)
                            if image.size in {(1280, 720), (405, 720)}:
                                rgb = image.convert("RGB")
                                padded_frames.append(
                                    (image.size, rgb.getpixel((image.width // 2, 5)))
                                )
                        sensor = "camera"
                    else:
                        packet = json.loads(payload)
                        packets.append(packet)
                        if packet.get("type") == "configure":
                            counts.clear()
                            ws.send(json.dumps({"type": "ready", "device_id": "phone"}))
                            return
                        sensor = packet["sensor"]
                    counts[sensor] = counts.get(sensor, 0) + 1
                    ws.send(
                        json.dumps(
                            {
                                "type": "published",
                                "sensor": sensor,
                                "count": counts[sensor],
                                "topic": "test." + sensor,
                                "bytes": 100,
                            }
                        )
                    )

                ws.on_message(receive)

            page.route_web_socket("**/ws", intercept)
            page.goto(address)
            page.wait_for_function("document.querySelectorAll('.sensor-card').length === 6")
            assert page.locator("#broker").input_value() == "amqp://guest:guest@10.10.50.176:30000"
            assert page.locator("#calibration-resolution option").evaluate_all(
                "options => options.map(option => option.value)"
            ) == page.locator("#camera-publish-resolution option").evaluate_all(
                "options => options.map(option => option.value).filter(value => value !== 'source')"
            )
            page.locator("#calibration-resolution").select_option("1920x1440")
            assert page.locator("#camera-publish-resolution").input_value() == "1920x1440"
            page.locator("#broker").fill("amqp://test:do-not-save@broker:5672")
            page.locator(".sensor-card").first.locator(".rate").fill("30")
            page.locator(".enabled").evaluate_all(
                """inputs => inputs.forEach(input => {
                    input.checked=true;
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                })"""
            )
            page.locator("#start").click()
            page.wait_for_function(
                "document.querySelector('#connection-state').textContent.includes('Conectado')"
            )
            page.evaluate("""window.dispatchEvent(new DeviceMotionEvent('devicemotion', {
                acceleration: {x: 1, y: 2, z: 3}, rotationRate: {alpha: 180, beta: 90, gamma: 0}}));
                window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {
                alpha: 90, beta: 0, gamma: 0, absolute: false}));""")
            page.wait_for_function("document.querySelector('video').videoWidth > 0")
            page.wait_for_function(
                "document.querySelectorAll('.sensor-count')[0].textContent !== '0 msgs'"
            )
            assert {packet.get("sensor") for packet in packets} >= {
                "accelerometer",
                "gyroscope",
                "orientation",
                "gps",
                "battery",
            }
            assert "do-not-save" not in page.evaluate(
                "localStorage.getItem('phone-gateway-settings-v1')"
            )
            assert page.locator("#broker").is_disabled()
            page.locator("#calibration-open").click()
            assert "Pare a publicação" in page.locator("#calibration-status").text_content()
            page.screenshot(path="/tmp/phone-gateway-desktop.png", full_page=True)
            page.locator("#stop").click()
            assert page.locator("#start").is_enabled()
            assert page.evaluate("document.querySelector('video').srcObject === null")
            page.locator("#start").click()
            page.wait_for_function(
                "document.querySelectorAll('.sensor-count')[0].textContent !== '0 msgs'"
            )
            page.locator("#stop").click()
            page.evaluate("""void (navigator.mediaDevices.getUserMedia = async () => {
                throw new DOMException('Denied for test', 'NotAllowedError');
            });""")
            page.locator("#calibration-open").click()
            page.wait_for_function(
                "document.querySelector('#calibration-status').textContent.includes('Permissão')"
            )
            page.evaluate("""navigator.mediaDevices.getUserMedia = async () => {
                const response = await fetch('/api/calibration/board');
                const bitmap = await createImageBitmap(await response.blob());
                const canvas = document.createElement('canvas');
                canvas.width = 1280; canvas.height = 960;
                const ctx = canvas.getContext('2d');
                const draw = () => ctx.drawImage(bitmap, 0, 0, 1280, 960);
                draw(); setInterval(draw, 100);
                return canvas.captureStream(30);
            };""")
            page.locator("#camera-publish-resolution").select_option("1280x720")
            page.locator("#start").click()
            page.wait_for_function(
                "document.querySelectorAll('.sensor-count')[0].textContent !== '0 msgs'"
            )
            page.locator("#stop").click()
            assert (1280, 720) in camera_sizes, camera_sizes
            assert any(size == (1280, 720) for size, _ in padded_frames)
            page.evaluate("""() => {
                const select = document.querySelector('#camera-publish-resolution');
                select.add(new Option('405 × 720', '405x720'));
            }""")
            page.locator("#camera-publish-resolution").select_option("405x720")
            page.locator("#start").click()
            page.wait_for_function(
                "document.querySelectorAll('.sensor-count')[0].textContent !== '0 msgs'"
            )
            page.locator("#stop").click()
            assert (405, 720) in camera_sizes, camera_sizes
            assert any(size == (405, 720) and max(pixel) < 10 for size, pixel in padded_frames)
            page.locator("#calibration-resolution").select_option("1280x960")
            page.locator("#calibration-open").click()
            page.wait_for_function("document.querySelector('#calibration-video').videoWidth > 0")
            page.locator("#calibration-capture").click()
            page.wait_for_function(
                "document.querySelector('#calibration-count').textContent === '1/5 fotos válidas'"
            )
            assert "1280 × 960" in page.locator("#calibration-source").text_content()
            page.screenshot(path="/tmp/phone-gateway-desktop.png", full_page=True)
            page.locator("#calibration-stop").click()
            assert not errors, errors
            mobile = browser.new_context(
                viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
            )
            mobile_page = mobile.new_page()
            mobile_page.goto(address)
            mobile_page.wait_for_function("document.querySelectorAll('.sensor-card').length === 6")
            assert mobile_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            mobile_page.screenshot(path="/tmp/phone-gateway-mobile.png", full_page=True)
            browser.close()
            print(
                "Interface OK: desktop, celular, câmera, cinco sensores, "
                "calibração ChArUco, preservação do quadro, início/parada e credenciais."
            )
            print("Capturas: /tmp/phone-gateway-desktop.png e /tmp/phone-gateway-mobile.png")
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
