from flask import Flask, request, Response
import os, requests, random, json
from flask_cors import CORS
from dotenv import load_dotenv

# load ARCGIS_URL from .env or hard-code it
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
# ARCGIS_URL = "https://services-eu1.arcgis.com/FckSU1kja7wbnBnq/arcgis/rest/services/Landmarks/FeatureServer/0"

app = Flask(__name__)
CORS(app)

@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    # detect if this is a feature‐query (has where or POST)
    is_query = request.method == "POST" or "where" in request.args
    if not is_query:
        # metadata passthrough
        upstream = requests.get(ARCGIS_URL, params=request.args)
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )

    # build base params
    params      = request.args.to_dict(flat=True)
    where       = params.get("where", "1=1")
    out_fields  = params.get("outFields", "*")

    # paging + per‐page sample settings
    page_size        = 2000
    sample_per_batch = int(params.get("batchSample", 300))

    sampled = []       # will hold the union of all per‐page samples
    offset  = 0

    while True:
        # page through
        resp = requests.get(
            f"{ARCGIS_URL}/query",
            params={
                "where":              where,
                "outFields":          out_fields,
                "resultRecordCount":  page_size,
                "resultOffset":       offset,
                "f":                  "json"
            }
        )
        data  = resp.json()
        batch = data.get("features", [])
        if not batch:
            break

        # sample up to sample_per_batch from this page
        k = min(sample_per_batch, len(batch))
        sampled.extend(random.sample(batch, k))

        # if fewer than a full page, we’re done
        if len(batch) < page_size:
            break

        offset += len(batch)

    # build a minimal GeoJSON‐style response
    payload = {
        "objectIdFieldName": data.get("objectIdFieldName", ""),
        "geometryType":      data.get("geometryType", ""),
        "spatialReference":  data.get("spatialReference", {}),
        "fields":            data.get("fields", []),
        "features":          sampled
    }

    return Response(
        json.dumps(payload),
        status=200,
        content_type="application/json"
    )

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
