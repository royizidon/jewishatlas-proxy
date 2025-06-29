import os
import math
import json
import random
import logging
from collections import defaultdict
from datetime import datetime

import requests
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("landmarks-proxy")

# Load environment
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").rstrip("/")
if not ARCGIS_URL:
    logger.error("ARCGIS_URL not set in .env")

# Flask setup
app = Flask(__name__)
CORS(app)


def create_clusters(features, zoom_level):
    """Cluster features for zoom < 10, otherwise return individual points."""
    logger.debug(f"CLUSTER: zoom={zoom_level}, input features={len(features)}")

    # Zoom ≥ 10 → individual points
    if zoom_level >= 10:
        logger.info("HIGH ZOOM: returning individual points")
        for feat in features:
            props = feat.setdefault("properties", {})
            props["is_cluster"] = False
            props["cluster_count"] = 1
            props.setdefault("Name", props.get("eng_name", "Unknown Site"))
        return features

    # Determine cluster radius (km → degrees)
    if zoom_level <= 6:
        km = 100
    elif zoom_level <= 8:
        km = 50
    else:
        km = 20
    radius_deg = km / 111.0
    logger.debug(f"LOW ZOOM: clustering with radius {km}km ({radius_deg:.4f}°)")

    clusters = []
    unclustered = features[:]
    cluster_id = 0

    while unclustered:
        seed = unclustered.pop(0)
        coords = seed.get("geometry", {}).get("coordinates", [0, 0])
        if len(coords) < 2:
            continue

        bucket = [seed]
        center_x, center_y = coords
        rest = []

        for f in unclustered:
            cx, cy = f.get("geometry", {}).get("coordinates", [0, 0])
            if len((cx, cy)) < 2:
                rest.append(f)
                continue
            dist = math.hypot(cx - center_x, cy - center_y)
            if dist <= radius_deg:
                bucket.append(f)
                # recompute centroid
                center_x = sum(p["geometry"]["coordinates"][0] for p in bucket) / len(bucket)
                center_y = sum(p["geometry"]["coordinates"][1] for p in bucket) / len(bucket)
            else:
                rest.append(f)

        unclustered = rest

        if len(bucket) == 1:
            f = bucket[0]
            props = f.setdefault("properties", {})
            props["is_cluster"] = False
            props["cluster_count"] = 1
            props.setdefault("Name", props.get("eng_name", "Unknown Site"))
            clusters.append(f)
        else:
            # tally categories
            cat_count = defaultdict(int)
            for p in bucket:
                cat_count[p.get("properties", {}).get("main_category", "Unknown")] += 1
            dom = max(cat_count.items(), key=lambda x: x[1])[0]

            clusters.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [center_x, center_y]},
                "properties": {
                    "OBJECTID": f"cluster_{cluster_id}",
                    "is_cluster": True,
                    "cluster_count": len(bucket),
                    "main_category": dom,
                    "categories": dict(cat_count),
                    "Name": f"Cluster of {len(bucket)} Sites",
                    "eng_name": f"Cluster ({len(bucket)})",
                    "Address": f"{len(bucket)} sites"
                }
            })
            cluster_id += 1

    logger.info(f"CLUSTER RESULT: {len(clusters)} total features")
    return clusters


def process_features(raw_json, zoom_level=10, viewport_bounds=None):
    """Parse GeoJSON, sample, cluster, and return new GeoJSON string."""
    data = json.loads(raw_json)
    feats = data.get("features", [])
    orig = len(feats)
    logger.debug(f"PROCESS: {orig} features at zoom {zoom_level}")

    if orig == 0:
        return raw_json

    # seed sampling for reproducibility
    if viewport_bounds:
        key = "{west:.3f},{south:.3f},{east:.3f},{north:.3f}".format(**viewport_bounds)
        random.seed(hash(key) % (2**32))

    # sampling cap
    max_pts = 1000
    if len(feats) > max_pts and zoom_level >= 10:
        logger.debug(f"SAMPLING: cap to {max_pts}")
        feat_cat = [f for f in feats if f.get("properties", {}).get("main_category") == "Featured"]
        others = [f for f in feats if f not in feat_cat]
        slot = max_pts - len(feat_cat)
        sampled = feat_cat + (others if len(others)<=slot else random.sample(others, slot))
        feats = sampled

    clustered = create_clusters(feats, zoom_level)
    data["features"] = clustered

    # metadata
    meta = {
        "processed": True,
        "original_count": orig,
        "returned_count": len(clustered),
        "cluster_count": sum(1 for f in clustered if f["properties"].get("is_cluster")),
        "point_count": sum(1 for f in clustered if not f["properties"].get("is_cluster")),
        "zoom_level": zoom_level,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    data["metadata"] = meta

    return json.dumps(data)


@app.route("/api/landmarks", defaults={"subpath": ""}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """Proxy & cluster ArcGIS queries."""
    is_query = request.method == "POST" or "where" in request.args
    if not is_query:
        # just proxy metadata calls
        resp = requests.get(ARCGIS_URL, params=request.args, timeout=30)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type"))

    # Build endpoint & params
    endpoint = f"{ARCGIS_URL}/query"
    params = request.args.to_dict()
    zoom = int(params.pop("zoom", 10))

    # handle envelope
    bounds = None
    if all(k in params for k in ("xmin","ymin","xmax","ymax")):
        xmin, ymin = float(params.pop("xmin")), float(params.pop("ymin"))
        xmax, ymax = float(params.pop("xmax")), float(params.pop("ymax"))
        # detect WebMercator
        if abs(xmin) > 180 or abs(ymin) > 90:
            def to_wgs(x, y):
                lon = x / 20037508.34 * 180
                lat = y / 20037508.34 * 180
                lat = 180/math.pi*(2*math.atan(math.exp(lat*math.pi/180)) - math.pi/2)
                return lon, lat
            xmin, ymin = to_wgs(xmin, ymin)
            xmax, ymax = to_wgs(xmax, ymax)
        bounds = {"west": xmin, "south": ymin, "east": xmax, "north": ymax}
        params.update({
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326", "outSR": "4326"
        })

    # enforce GeoJSON + all fields
    params.update({
        "f": "geojson",
        "outFields": "*",
        "resultRecordCount": "5000"
    })

    # dispatch to ArcGIS
    if request.method == "GET":
        upstream = requests.get(endpoint, params=params, timeout=30)
    else:
        upstream = requests.post(endpoint, data=request.get_data(), headers={"Content-Type": request.content_type}, timeout=30)

    if upstream.status_code != 200:
        return jsonify({"error": "ArcGIS request failed"}), 502

    content = upstream.content.decode("utf-8")
    if '"features"' not in content:
        return Response(upstream.content, status=200, content_type="application/json")

    clustered = process_features(content, zoom, bounds)
    return Response(clustered, status=200, content_type="application/json")


@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="healthy", arcgis=ARCGIS_URL, ts=datetime.utcnow().isoformat()+"Z")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting server at 0.0.0.0:{port}, ARCGIS_URL={ARCGIS_URL}")
    app.run(host="0.0.0.0", port=port, debug=True)
