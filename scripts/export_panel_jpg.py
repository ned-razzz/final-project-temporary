#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageFilter


def parse_args():
    parser = argparse.ArgumentParser(description="Render panel.html to a JPG image.")
    parser.add_argument("--input", default="panel.html", help="HTML file to render")
    parser.add_argument("--output", default="panel.jpg", help="JPG output path")
    parser.add_argument("--width", type=int, default=1500, help="Viewport width")
    parser.add_argument("--height", type=int, default=2120, help="Viewport height")
    parser.add_argument("--quality", type=int, default=100, help="JPG quality, 1-100")
    parser.add_argument("--dpi", type=int, default=300, help="JPG DPI metadata")
    parser.add_argument(
        "--scale",
        type=float,
        default=3,
        help="Output scale factor. Higher values preserve more detail at larger pixel dimensions.",
    )
    parser.add_argument(
        "--sharpen",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply light sharpening before JPG encoding.",
    )
    return parser.parse_args()


class DevToolsWebSocket:
    def __init__(self, url):
        parsed = urllib.parse.urlparse(url)
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path
        if parsed.query:
            self.path += "?" + parsed.query
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("Could not connect to Chrome DevTools websocket.")
        self.next_id = 1

    def close(self):
        self.sock.close()

    def _read_exact(self, size):
        chunks = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RuntimeError("Chrome DevTools websocket closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_json(self, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(data)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(data)))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(header + masked)

    def _recv_json(self):
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            masked = second & 0x80
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("Chrome DevTools websocket closed.")
            if opcode == 0x9:
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def call(self, method, params=None, timeout=10):
        message_id = self.next_id
        self.next_id += 1
        self._send_json({"id": message_id, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_json()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        raise TimeoutError(f"Timed out waiting for {method}.")

    def wait_for_event(self, method, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_json()
            if message.get("method") == method:
                return message
        raise TimeoutError(f"Timed out waiting for {method}.")


def read_devtools_url(process, timeout=10):
    pattern = re.compile(r"DevTools listening on (ws://[^\s]+)")
    deadline = time.time() + timeout
    lines = []
    while time.time() < deadline:
        line = process.stderr.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        lines.append(line)
        match = pattern.search(line)
        if match:
            return match.group(1)
    raise RuntimeError("Could not find Chrome DevTools endpoint.\n" + "".join(lines))


def create_page(port, url):
    endpoint = f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/')}"
    request = urllib.request.Request(endpoint, method="PUT")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))["webSocketDebuggerUrl"]


def render_with_chrome(chrome, input_path, png_path, width, height, scale):
    process = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--no-sandbox",
            f"--force-device-scale-factor={scale}",
            "--remote-debugging-port=0",
            "about:blank",
        ],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    websocket = None
    try:
        browser_url = read_devtools_url(process)
        port = urllib.parse.urlparse(browser_url).port
        page_url = create_page(port, input_path.as_uri())
        websocket = DevToolsWebSocket(page_url)
        websocket.call("Page.enable")
        websocket.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": scale,
                "mobile": False,
            },
        )
        websocket.call("Page.navigate", {"url": input_path.as_uri()})
        websocket.wait_for_event("Page.loadEventFired", timeout=20)
        websocket.call(
            "Runtime.evaluate",
            {
                "expression": (
                    "Promise.all(["
                    "document.fonts ? document.fonts.ready : Promise.resolve(),"
                    "Promise.all(Array.from(document.images).map((img) => "
                    "img.complete ? Promise.resolve() : new Promise((resolve) => {"
                    "img.addEventListener('load', resolve, {once: true});"
                    "img.addEventListener('error', resolve, {once: true});"
                    "})))"
                    "])"
                ),
                "awaitPromise": True,
                "returnByValue": True,
            },
            timeout=20,
        )
        time.sleep(0.25)
        metrics = websocket.call(
            "Runtime.evaluate",
            {
                "expression": (
                    "(() => {"
                    "const d = document.documentElement;"
                    "const b = document.body;"
                    "return {"
                    "width: Math.ceil(Math.max(d.scrollWidth, b.scrollWidth, d.offsetWidth, b.offsetWidth, d.clientWidth)),"
                    "height: Math.ceil(Math.max(d.scrollHeight, b.scrollHeight, d.offsetHeight, b.offsetHeight, d.clientHeight))"
                    "};"
                    "})()"
                ),
                "returnByValue": True,
            },
        )["result"]["value"]
        capture_width = max(width, int(metrics["width"]))
        capture_height = max(height, int(metrics["height"]))
        websocket.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": capture_width,
                "height": capture_height,
                "deviceScaleFactor": scale,
                "mobile": False,
            },
        )
        screenshot = websocket.call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": capture_width,
                    "height": capture_height,
                    "scale": 1,
                },
            },
            timeout=30,
        )
        png_path.write_bytes(base64.b64decode(screenshot["data"]))
    finally:
        if websocket:
            websocket.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def prepare_for_jpeg(image, sharpen):
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    if sharpen:
        image = image.filter(ImageFilter.UnsharpMask(radius=0.6, percent=115, threshold=2))

    return image


def main():
    args = parse_args()
    if args.quality < 1 or args.quality > 100:
        raise SystemExit("--quality must be between 1 and 100.")
    if args.scale <= 0:
        raise SystemExit("--scale must be greater than 0.")
    if args.dpi <= 0:
        raise SystemExit("--dpi must be greater than 0.")

    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    wkhtmltoimage = shutil.which("wkhtmltoimage")
    if not chrome and not wkhtmltoimage:
        raise SystemExit("Chrome, Chromium, or wkhtmltoimage is required.")

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        png_path = Path(tmpdir) / "panel.png"
        try:
            if not chrome:
                raise RuntimeError("Chrome or Chromium was not found.")
            render_with_chrome(chrome, input_path, png_path, args.width, args.height, args.scale)
        except (subprocess.CalledProcessError, RuntimeError, TimeoutError):
            if not wkhtmltoimage:
                raise
            cmd = [
                wkhtmltoimage,
                "--format",
                "png",
                "--enable-local-file-access",
                "--width",
                str(round(args.width * args.scale)),
                "--disable-smart-width",
                "--zoom",
                str(args.scale),
                input_path.as_uri(),
                str(png_path),
            ]
            subprocess.run(cmd, check=True)

        with Image.open(png_path) as image:
            prepare_for_jpeg(image, args.sharpen).save(
                output_path,
                "JPEG",
                quality=args.quality,
                optimize=True,
                subsampling=0,
                progressive=True,
                dpi=(args.dpi, args.dpi),
            )

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
