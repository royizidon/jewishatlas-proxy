from flask import Flask, request, Response, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv
import json
import random

# ─── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").rstrip("/")
if not ARCGIS_URL:
    raise RuntimeError("Missing ARCGIS_URL in .env")

app = Flask(__name__)
CORS(app)


# ─── Proxy + Sampling Endpoint ─────────────────────────────────────────────────
@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    """
    Proxies *every* request to <ARCGIS_URL>/query but forces:
      • f=geojson
      • returnGeometry=true
      • outFields=*
      • where=1=1 (if not provided)
    Then, if the GeoJSON has more than 3000 features, randomly samples 3000.
    """
    endpoint = f"{ARCGIS_URL}/query"

    # 1) Build our outgoing params (for GET) or body (for POST)
    if request.method == "GET":
        params = request.args.to_dict(flat=True)
        # enforce the essentials
        params["f"] = "geojson"
        params.setdefault("where", "1=1")
        params["outFields"] = "*"
        params["returnGeometry"] = "true"
        upstream = requests.get(endpoint, params=params, timeout=30)

    else:  # POST
        # assume JSON body
        try:
            body = request.get_json(force=True)
        except Exception:
            body = {}
        body["f"] = "geojson"
        body.setdefault("where", "1=1")
        body["outFields"] = "*"
        body["returnGeometry"] = "true"
        upstream = requests.post(
            endpoint,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30
        )

    # 2) If ArcGIS errored, just pass it through
    if upstream.status_code != 200:
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )

    content_type = upstream.headers.get("Content-Type", "")
    payload = upstream.content

    # 3) If it’s GeoJSON, sample features down to 3000
    if "application/json" in content_type:
        try:
            data = json.loads(upstream.content)
            features = data.get("features")
            if isinstance(features, list) and len(features) > 3000:
                data["features"] = random.sample(features, 3000)
            payload = json.dumps(data).encode("utf-8")
        except Exception:
            # parse error? fall back to the raw payload
            pass

    # 4) Return the (possibly sampled) GeoJSON
    return Response(
        payload,
        status=200,
        content_type="application/json"
    )


# ─── Health Check ──────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="healthy", arcgis_url=ARCGIS_URL), 200


# ─── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
