from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

# 1. Load & clean your layer URL (no /query at the end)
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

app = Flask(__name__)
CORS(app)

# 2. Catch both the base path and ANY sub-path under /api/landmarks
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>",                methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """
    - metadata requests (no 'where' + GET) → ARCGIS_URL?f=json
    - feature queries (has 'where' or it's a POST) → ARCGIS_URL/query
    """
    is_query = request.method == "POST" or "where" in request.args

    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        if request.method == "GET":
            upstream = requests.get(endpoint, params=request.args)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")}
            )
    else:
        # layer metadata
        upstream = requests.get(ARCGIS_URL, params=request.args)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
