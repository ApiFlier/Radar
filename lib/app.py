import os
import requests
from flask import Flask, Response, request, render_template

app = Flask(__name__)

API_HOST = os.getenv("API_HOST", "api")
API_PORT = os.getenv("API_PORT", "8081")
API_BASE = f"http://{API_HOST}:{API_PORT}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
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
