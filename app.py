# create and open in your editor
from flask import Flask, request, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()    # load ARCGIS_URL from .env

app = Flask(__name__)
CORS(app)        # allow requests from your front-end

ARCGIS_URL = os.getenv("ARCGIS_URL")

@app.route("/api/landmarks")
def proxy_landmarks():
    # forward every query param to the real ArcGIS URL
    r = requests.get(ARCGIS_URL, params=request.args)
    return jsonify(r.json()), r.status_code

if __name__ == "__main__":
    # use PORT env var on Render, default to 5000 locally
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

