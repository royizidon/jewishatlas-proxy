from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
raw_url = os.getenv("ARCGIS_URL", "")
ARCGIS_URL = raw_url.strip().replace("\n", "").replace("\r", "")
app = Flask(__name__)
CORS(app)

@app.route("/api/landmarks", methods=["GET", "POST"])
def proxy_landmarks():
    if request.method == "GET":
        upstream = requests.get(ARCGIS_URL, params=request.args)
    else:
        upstream = requests.post(
            ARCGIS_URL,
            data=request.get_data(),
            headers={"Content-Type": request.headers.get("Content-Type", "application/json")}
        )

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
