import os
import json
import random
import requests
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ──────────────── setup ──────────────────
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").rstrip("/")
if not ARCGIS_URL:
    raise RuntimeError("Please set ARCGIS_URL in your .env")

app = Flask(__name__)
CORS(app)


# ──────────────── main proxy ──────────────────
@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    """
    Proxies every request to ARCGIS_URL/query, then:
      • If it’s valid GeoJSON with >3000 features → random.sample to 3000
      • Else → passthrough unmodified
    """
    endpoint = f"{ARCGIS_URL}/query"

    # 1) Forward the request
    if request.method == "GET":
        # ensure we get geojson
        params = request.args.to_dict()
        params["f"] = "geojson"
        upstream = requests.get(endpoint, params=params, timeout=30)
    else:
        upstream = requests.post(
            endpoint,
            data=request.get_data(),
            headers={"Content-Type": request.headers.get("Content-Type")},
            timeout=30
        )

    # 2) If ArcGIS failed, just bubble up the error
    if upstream.status_code != 200:
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )

    # 3) If it’s JSON, try sampling
    content_type = upstream.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            data = upstream.json()
            feats = data.get("features")
            if isinstance(feats, list) and len(feats) > 3000:
                data["features"] = random.sample(feats, 3000)
            return Response(
                json.dumps(data),
                status=200,
                content_type="application/json"
            )
        except Exception:
            # parsing error? fall through to raw passthrough
            pass

    # 4) Non-JSON or parse error: passthrough
    return Response(
        upstream.content,
        status=200,
        content_type=content_type
    )


# ──────────────── health check ──────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="healthy", arcgis=ARCGIS_URL), 200


# ──────────────── run ──────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
