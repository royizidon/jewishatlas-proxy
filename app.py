from flask import Flask, request, Response
import os
import requests
from flask_cors import CORS
from dotenv import load_dotenv

# Load ARCGIS_URL from your environment (set this in Render):
#   ARCGIS_URL=https://services-eu1.arcgis.com/FckSU1kja7wbnBnq/arcgis/rest/services/Landmarks/FeatureServer/0
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

app = Flask(__name__)
CORS(app)

# Catch both /api/landmarks and any deeper path (e.g. /api/landmarks/0/query)
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>",                methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """
    - If it's a POST or includes a 'where' parameter, treat it as a feature query:
        → proxy to ARCGIS_URL + '/query'
    - Otherwise it's the layer metadata request:
        → proxy to ARCGIS_URL
    """
    is_query = request.method == "POST" or "where" in request.args

    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        if request.method == "GET":
            upstream = requests.get(endpoint, params=request.args)
        else:
            # forward the exact POST body and content-type
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")}
            )
    else:
        # metadata call (e.g. ?f=json)
        upstream = requests.get(ARCGIS_URL, params=request.args)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    # for local testing: use PORT env or default to 5000
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
