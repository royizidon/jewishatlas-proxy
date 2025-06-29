from flask import Flask, request, Response
import os, requests, random, time
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

app = Flask(__name__)
CORS(app)

# ————————————————
# CACHE CONFIGURATION
# ————————————————
COUNT_TTL       = 86400 * 2   # refresh every 2 days
_cached_count   = None
_last_count_ts  = 0

def get_total_count(where="1=1"):
    global _cached_count, _last_count_ts
    now = time.time()
    if _cached_count is None or (now - _last_count_ts) > COUNT_TTL:
        resp = requests.get(
            f"{ARCGIS_URL}/query",
            params={"where": where, "returnCountOnly": "true", "f": "json"}
        )
        resp.raise_for_status()
        _cached_count  = resp.json().get("count", 0)
        _last_count_ts = now
    return _cached_count

# ————————————————
# PROXY ENDPOINT
# ————————————————
@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    # always treat as a feature query
    args        = request.args.to_dict(flat=True)
    where       = args.get("where", "1=1")
    out_fields  = args.get("outFields", "*")
    sample_size = int(args.get("sample", 3000))

    # 1) grab the (cached) total count
    total = get_total_count(where)

    # 2) clamp to available features
    n = min(sample_size, total)
    if n == 0:
        return Response('{"features": []}', 200, mimetype="application/json")

    # 3) pick a random start so you get a window of n features
    max_offset = total - n
    offset     = random.randint(0, max_offset)

    # 4) do the single “window” query
    params = {
        "where":              where,
        "outFields":          out_fields,
        "resultRecordCount":  n,
        "resultOffset":       offset,
        "f":                  "json"
    }
    resp = requests.get(f"{ARCGIS_URL}/query", params=params)

    # 5) return ArcGIS’s JSON straight through
    return Response(
        resp.content,
        status=resp.status_code,
        content_type=resp.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
