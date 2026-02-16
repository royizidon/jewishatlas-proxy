from flask import Flask, request, Response, jsonify
import os
import json
import time
import requests
from flask_cors import CORS
from dotenv import load_dotenv

# =========================
# Load env
# =========================
load_dotenv()

ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()  # map layer
MEMORIAL_LAYER_URL = os.getenv("MEMORIAL_LAYER_URL", "").strip()  # wall layer

ARCGIS_USERNAME = os.getenv("ARCGIS_USERNAME", "").strip()
ARCGIS_PASSWORD = os.getenv("ARCGIS_PASSWORD", "").strip()

app = Flask(__name__)
CORS(app)

# =========================
# ArcGIS Token (cached)
# =========================
_TOKEN_CACHE = {"token": None, "expires": 0}  # expires is unix time (seconds)

def get_arcgis_token():
    """
    Generates an ArcGIS Online token using username/password.
    Caches token until near expiration.
    """
    if not ARCGIS_USERNAME or not ARCGIS_PASSWORD:
        raise RuntimeError("Missing ARCGIS_USERNAME or ARCGIS_PASSWORD in .env")

    now = time.time()
    if _TOKEN_CACHE["token"] and now < (_TOKEN_CACHE["expires"] - 60):
        return _TOKEN_CACHE["token"]

    url = "https://www.arcgis.com/sharing/rest/generateToken"
    payload = {
    "username": ARCGIS_USERNAME,
    "password": ARCGIS_PASSWORD,
    "client": "requestip",
    "expiration": 60,
    "f": "json",
    }


    r = requests.post(url, data=payload, timeout=30)
    data = r.json()

    if "token" not in data:
        raise RuntimeError(f"Token error: {data}")

    token = data["token"]
    # expires from AGO is in milliseconds since epoch
    expires_ms = data.get("expires", 0)
    expires_sec = int(expires_ms / 1000) if expires_ms else int(now + 55 * 60)

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires"] = expires_sec
    return token

# =========================
# Existing Map Proxy (UNCHANGED)
# =========================
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """
    - metadata requests (no 'where' + GET) → ARCGIS_URL?f=json
    - feature queries (has 'where' or it's a POST) → ARCGIS_URL/query
    """
    if not ARCGIS_URL:
        return Response(
            json.dumps({"error": "ARCGIS_URL not set"}),
            status=500,
            content_type="application/json",
        )

    is_query = request.method == "POST" or "where" in request.args

    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        if request.method == "GET":
            upstream = requests.get(endpoint, params=request.args)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")},
            )
    else:
        upstream = requests.get(ARCGIS_URL, params=request.args)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )

# =========================
# NEW: Test token quickly
# =========================
@app.route("/api/test-token", methods=["GET"])
def api_test_token():
    try:
        token = get_arcgis_token()
        return jsonify({"ok": True, "token_preview": token[:10]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =========================
# NEW: Wall list (published only)
# =========================
@app.route("/api/wall", methods=["GET"])
def api_wall():
    if not MEMORIAL_LAYER_URL:
        return jsonify({"error": "MEMORIAL_LAYER_URL not set"}), 500

    try:
        token = get_arcgis_token()

        params = {
            "where": "is_published = 1",
            "outFields": "slug,he_name,eng_name,born_str,death_str,born_date,death_date,origin,tier",
            "orderByFields": "updated_at DESC",
            "f": "json",
            "token": token,
        }

        upstream = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=30)
        return Response(upstream.content, status=upstream.status_code, content_type="application/json")

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# NEW: Dedicate (insert draft)
# =========================
@app.route("/api/dedicate", methods=["POST"])
def api_dedicate():

    if not MEMORIAL_LAYER_URL:
        return jsonify({"error": "MEMORIAL_LAYER_URL not set"}), 500

    try:
        token = get_arcgis_token()

        # --------------------------
        # Read form fields
        # --------------------------
        data = request.form

        slug = data.get("slug")
        he_name = data.get("he_name")
        eng_name = data.get("eng_name")

        if not slug:
            return jsonify({"error": "Missing slug"}), 400

        if not (he_name or eng_name):
            return jsonify({"error": "Name required"}), 400

        # --------------------------
        # Build attributes
        # --------------------------
        attrs = {
            "slug": slug,
            "he_name": he_name,
            "eng_name": eng_name,
            "born_str": data.get("born_str"),
            "death_str": data.get("death_str"),
            "origin": data.get("origin"),
            "full_bio": data.get("full_bio"),
            "tier": data.get("tier") or "brick",
            "memorial_type": "memory",
            "is_published": 0,
            "payment_status": "pending",
            "dedicator_email": data.get("dedicator_email"),
        }

        feature = {"attributes": attrs}

        payload = {
            "f": "json",
            "token": token,
            "adds": json.dumps([feature]),
        }

        # --------------------------
        # Insert feature
        # --------------------------
        insert_res = requests.post(
            f"{MEMORIAL_LAYER_URL}/addFeatures",
            data=payload,
            timeout=30
        )

        insert_json = insert_res.json()

        if not insert_json.get("addResults"):
            return jsonify({"error": insert_json}), 500

        add_result = insert_json["addResults"][0]

        if not add_result.get("success"):
            return jsonify({"error": insert_json}), 500

        object_id = add_result["objectId"]

        # --------------------------
        # Upload image (if exists)
        # --------------------------
        if "image" in request.files:
            file = request.files["image"]

            if file and file.filename:

                files = {
                    "attachment": (file.filename, file.stream, file.mimetype)
                }

                attach_payload = {
                    "f": "json",
                    "token": token
                }

                attach_res = requests.post(
                    f"{MEMORIAL_LAYER_URL}/{object_id}/addAttachment",
                    data=attach_payload,
                    files=files,
                    timeout=30
                )

                attach_json = attach_res.json()

                if not attach_json.get("addAttachmentResult", {}).get("success"):
                    return jsonify({"error": "Image upload failed", "details": attach_json}), 500

        return jsonify({
            "success": True,
            "objectId": object_id
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Run
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
