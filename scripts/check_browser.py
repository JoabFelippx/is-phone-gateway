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

            def intercept(ws):
                def receive(payload):
                    if isinstance(payload, bytes):
                        assert payload[8:10] == b"\xff\xd8"
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
            page.screenshot(path="/tmp/phone-gateway-desktop.png", full_page=True)
            page.locator("#stop").click()
            assert page.locator("#start").is_enabled()
            assert page.evaluate("document.querySelector('video').srcObject === null")
            page.locator("#start").click()
            page.wait_for_function(
                "document.querySelectorAll('.sensor-count')[0].textContent !== '0 msgs'"
            )
            page.locator("#stop").click()
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
                "início/parada e credenciais."
            )
            print("Capturas: /tmp/phone-gateway-desktop.png e /tmp/phone-gateway-mobile.png")
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
