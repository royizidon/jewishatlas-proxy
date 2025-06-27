from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
# Now ARCGIS_URL is the layer resource (no /query)
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()

app = Flask(__name__)
CORS(app)

@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    # if the JS API is asking for features (it sends a 'where' param),
    # or it's a POST, proxy to the /query endpoint:
    if request.method == "POST" or "where" in request.args:
        upstream_url = f"{ARCGIS_URL}/query"
        if request.method == "GET":
            upstream = requests.get(upstream_url, params=request.args)
        else:
            upstream = requests.post(
                upstream_url,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")}
            )
    else:
        # otherwise it's the metadata request: /FeatureServer/0?f=json
        upstream = requests.get(ARCGIS_URL, params=request.args)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
