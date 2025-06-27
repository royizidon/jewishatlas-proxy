from flask import Flask, request, Response
import os, random
import requests
from flask_cors import CORS
from dotenv import load_dotenv

# 1) Load your layer root (no /query)
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

# 2) Max points per view
MAX_RECORDS = 1000

app = Flask(__name__)
CORS(app)

# 3) Catch both /api/landmarks and any sub-path under it
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET","POST"])
@app.route("/api/landmarks/<path:subpath>",               methods=["GET","POST"])
def proxy_landmarks(subpath):
    # Build a params dict for GET or POST
    if request.method == "GET":
        params = request.args.to_dict(flat=True)
    else:
        # handle form-encoded or JSON bodies
        if request.is_json:
            params = request.get_json()
        else:
            params = request.form.to_dict(flat=True)

    # Detect spatial queries by presence of `geometry`
    is_spatial = "geometry" in params

    # If this is a spatial fetch, do the ID->sample->features pattern
    if is_spatial:
        query_url = f"{ARCGIS_URL}/query"
        # 3a) First, get IDs only (requires geometryType & spatialRelationship)
        id_params = {
            "geometry": params["geometry"],
            "geometryType": "esriGeometryEnvelope",
            "spatialRelationship": "esriSpatialRelIntersects",
            "returnIdsOnly": "true",
            "returnGeometry": "false",
            "f": "json"
        }
        # If client requested a where=, carry it through
        if "where" in params:
            id_params["where"] = params["where"]
        id_resp = requests.get(query_url, params=id_params)

        if id_resp.status_code == 200:
            ids = id_resp.json().get("objectIds") or []
            # 3b) sample
            if len(ids) > MAX_RECORDS:
                ids = random.sample(ids, MAX_RECORDS)
            # 3c) fetch those features
            feat_params = {
                "objectIds": ",".join(map(str, ids)),
                "outFields": params.get("outFields", "*"),
                "returnGeometry": "true",
                "f": "json"
            }
            upstream = requests.get(query_url, params=feat_params)
        else:
            # fallback: just cap the record count on the original spatial request
            params.setdefault("resultRecordCount", MAX_RECORDS)
            params.setdefault("f", "json")
            upstream = requests.get(query_url, params=params)

    else:
        # Non-spatial: metadata or where searches
        # decide whether to hit the layer root or /query
        if "where" in params:
            # a search/filter by where => go to /query
            params.setdefault("f", "json")
            query_url = f"{ARCGIS_URL}/query"
            upstream = requests.get(query_url, params=params)
        else:
            # metadata (e.g. ?f=json)
            upstream = requests.get(ARCGIS_URL, params=params)

    # Return the raw response
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
