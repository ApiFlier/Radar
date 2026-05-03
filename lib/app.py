import os
import requests
from flask import Flask, Response, request, render_template, render_template_string

app = Flask(__name__)

_SOON = """<!doctype html><html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar — {{ title }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#111;color:#ccc;font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh}
.box{text-align:center}
.ttl{font-size:20px;font-weight:700;color:#00ffcc;margin-bottom:6px}
.sub{font-size:13px;color:#3a3a3a;margin-bottom:22px}
a{color:#00ffcc;font-size:12px;text-decoration:none;border:1px solid rgba(0,255,204,.25);padding:6px 18px;border-radius:4px}
a:hover{background:rgba(0,255,204,.07)}
</style>
</head>
<body><div class="box">
<div class="ttl">{{ title }}</div>
<div class="sub">Coming soon</div>
<a href="/">&#8592; Radar</a>
</div></body></html>"""

API_HOST = os.getenv("API_HOST", "api")
API_PORT = os.getenv("API_PORT", "8081")
API_BASE = f"http://{API_HOST}:{API_PORT}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stats")
def stats():
    return render_template("stats.html")


@app.route("/airports")
def airports():
    return render_template_string(_SOON, title="Airports")

@app.route("/aircraft")
def aircraft():
    return render_template_string(_SOON, title="Aircraft")

@app.route("/ground")
def ground():
    return render_template_string(_SOON, title="Ground Ops")

@app.route("/alerts")
def alerts():
    return render_template_string(_SOON, title="Alerts")

@app.route("/settings")
def settings():
    return render_template_string(_SOON, title="Settings")


@app.route("/health")
def health():
    return render_template("health.html")

@app.route("/health.json")
def health_json():
    return {
        "status": "ok",
        "service": "flight-web",
        "api_base": API_BASE,
    }


def proxy_to_api(path):
    url = f"{API_BASE}/{path}"

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            params=request.args,
            data=request.get_data(),
            headers={
                key: value
                for key, value in request.headers
                if key.lower() not in ("host", "content-length")
            },
            stream=True,
            timeout=None if path == "api/stream" else 30,
        )

        excluded_headers = {
            "content-encoding",
            "content-length",
            "transfer-encoding",
            "connection",
        }

        headers = [
            (name, value)
            for name, value in resp.raw.headers.items()
            if name.lower() not in excluded_headers
        ]

        return Response(
            resp.iter_content(chunk_size=8192),
            status=resp.status_code,
            headers=headers,
            content_type=resp.headers.get("content-type"),
        )

    except requests.RequestException as exc:
        return {
            "status": "error",
            "message": "Unable to reach API service",
            "api_base": API_BASE,
            "error": str(exc),
        }, 502



@app.route("/api/health")
def api_health():
    return proxy_to_api("health")

@app.route("/api/stream")
def api_stream():
    return proxy_to_api("api/stream")


@app.route("/api/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_proxy(path):
    if not path:
        return proxy_to_api("")
    return proxy_to_api(f"api/{path}")


@app.route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_proxy_no_slash():
    return proxy_to_api("")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
