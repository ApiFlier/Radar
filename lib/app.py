import os
import httpx
from flask import Flask, render_template, request, Response, jsonify

app = Flask(__name__, template_folder="templates")

API_HOST = os.getenv("API_HOST", "api")
API_PORT = os.getenv("API_PORT", "8081")
API_BASE = f"http://{API_HOST}:{API_PORT}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    try:
        resp = httpx.get(f"{API_BASE}/health", timeout=10)
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/json")
        )
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 502


@app.route("/api/stream")
def api_stream():
    def generate():
        with httpx.stream("GET", f"{API_BASE}/api/stream", timeout=None) as resp:
            for line in resp.iter_lines():
                if line is None:
                    continue
                yield line + "\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def api_proxy():
    url = f"{API_BASE}/?{request.query_string.decode()}"

    try:
        if request.method == "GET":
            resp = httpx.get(url, timeout=30)
        else:
            resp = httpx.request(
                request.method,
                url,
                json=request.get_json(silent=True),
                timeout=30
            )

        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", "application/json")
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
