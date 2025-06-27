from flask import Flask, request, Response
import os, random
import requests
from flask_cors import CORS
from dotenv import load_dotenv

# Load your layer root (no /query)
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

# Max number of points to return per view
MAX_RECORDS = 1000

app = Flask(__name__)
CORS(app)

@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET","POST"])
@app.route("/api/landmarks/<path:subpath>",               methods=["GET","POST"])
def proxy_landmarks(subpath):
    # Gather params from GET or POST
    if request.method == "GET":
        params = request.args.to_dict(flat=True)
    else:
        # support JSON or form bodies
        if request.is_json:
            params = request.get_json()
        else:
            params = request.form.to_dict(flat=True)

    # Detect a spatial (extent) query
    is_spatial = "geometry" in params

    if is_spatial:
        # 1) Get only the IDs in the extent
        id_params = params.copy()
        id_params.pop("outFields", None)
        id_params["returnIdsOnly"] = True
        id_params["returnGeometry"] = False
        id_params["f"] = "json"

        id_url = f"{ARCGIS_URL}/query"
        id_resp = requests.get(id_url, params=id_params)
        if id_resp.status_code != 200:
            # fallback: limit count instead of sampling
            params.setdefault("resultRecordCount", MAX_RECORDS)
            upstream = requests.get(id_url, params=params)
        else:
            ids = id_resp.json().get("objectIds", [])
            # 2) Random sample up to MAX_RECORDS
            if len(ids) > MAX_RECORDS:
                ids = random.sample(ids, MAX_RECORDS)
            # 3) Fetch those features
            feat_params = {
                "objectIds": ",".join(map(str, ids)),
                "outFields": params.get("outFields", "*"),
                "returnGeometry": True,
                "f": "json"
            }
            upstream = requests.get(id_url, params=feat_params)
    else:
        # Non-spatial (metadata or search where=) – proxy directly
        upstream = requests.get(ARCGIS_URL, params=params)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
