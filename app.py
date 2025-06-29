from flask import Flask, request, Response, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv
import json
import random

# 1. Load & clean your layer URL (no /query at the end)
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
app = Flask(__name__)
CORS(app)

def limit_features_randomly(response_data, viewport_bounds=None, zoom_level=10, max_features=1000):
    """Smart sampling based on zoom level and consistent viewport seeding"""
    try:
        data = json.loads(response_data)
        
        if 'features' not in data or len(data['features']) <= max_features:
            return response_data
        
        features = data['features']
        
        # Create consistent seed from viewport bounds for same area = same points
        if viewport_bounds:
            seed_str = f"{viewport_bounds['west']:.3f},{viewport_bounds['south']:.3f},{viewport_bounds['east']:.3f},{viewport_bounds['north']:.3f}"
            seed = hash(seed_str) % (2**32)
            random.seed(seed)
        
        # Adjust max features based on zoom level
        if zoom_level >= 15:  # Street level - more detail
            adjusted_max = min(max_features * 2, 2000)
        elif zoom_level >= 12:  # Neighborhood level
            adjusted_max = max_features
        elif zoom_level >= 8:   # City level
            adjusted_max = max_features // 2
        else:  # Region level - fewer points
            adjusted_max = max_features // 4
        
        # Smart sampling based on zoom and importance
        if zoom_level >= 12:
            # At neighborhood/street level: prioritize important features + random sample
            important_features = []
            regular_features = []
            
            for feature in features:
                importance = feature.get('properties', {}).get('importance', 0)
                category = feature.get('properties', {}).get('main_category', '')
                
                # Prioritize Featured and high importance
                if category == 'Featured' or importance >= 8:
                    important_features.append(feature)
                else:
                    regular_features.append(feature)
            
            # Include all important features first
            sampled_features = important_features[:adjusted_max]
            
            # Fill remaining slots with random selection from regular features
            remaining_slots = adjusted_max - len(sampled_features)
            if remaining_slots > 0 and regular_features:
                random_regular = random.sample(regular_features, min(remaining_slots, len(regular_features)))
                sampled_features.extend(random_regular)
                
        else:
            # At city/region level: only show most important features
            # Sort by importance and category priority
            def get_priority(feature):
                props = feature.get('properties', {})
                category = props.get('main_category', '')
                importance = props.get('importance', 0)
                
                # Priority scoring
                if category == 'Featured':
                    return 1000 + importance
                elif category == 'Synagogue':
                    return 800 + importance
                elif category == 'Heritage':
                    return 600 + importance
                else:
                    return importance
            
            sorted_features = sorted(features, key=get_priority, reverse=True)
            sampled_features = sorted_features[:adjusted_max]
        
        # Update data with sampled features
        data['features'] = sampled_features
        
        # Add metadata about sampling
        if 'metadata' not in data:
            data['metadata'] = {}
        data['metadata'].update({
            'sampled': True,
            'original_count': len(features),
            'returned_count': len(sampled_features),
            'zoom_level': zoom_level,
            'sampling_strategy': 'smart_zoom_based'
        })
        
        return json.dumps(data)
        
    except Exception as e:
        # If anything goes wrong, return original data
        print(f"Sampling error: {e}")
        return response_data

# 2. Catch both the base path and ANY sub-path under /api/landmarks
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>",                methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """
    - metadata requests (no 'where' + GET) → ARCGIS_URL?f=json
    - feature queries (has 'where' or it's a POST) → ARCGIS_URL/query
    """
    is_query = request.method == "POST" or "where" in request.args
    
    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        
        # Modify parameters to add spatial filter if viewport bounds provided
        if request.method == "GET":
            params = dict(request.args)
            
            # Add spatial filter if viewport bounds are provided
            if all(param in params for param in ['xmin', 'ymin', 'xmax', 'ymax']):
                # Create envelope geometry for spatial filter
                envelope = f"{params['xmin']},{params['ymin']},{params['xmax']},{params['ymax']}"
                params['geometry'] = envelope
                params['geometryType'] = 'esriGeometryEnvelope'
                params['spatialRel'] = 'esriSpatialRelIntersects'
                params['inSR'] = '3857'  # Changed from 4326 to 3857 (Web Mercator)
                
                # Remove the individual bound parameters since we've used them
                for bound_param in ['xmin', 'ymin', 'xmax', 'ymax']:
                    params.pop(bound_param, None)
            
            # Ensure we're getting GeoJSON format
            if 'f' not in params:
                params['f'] = 'geojson'
            
            # Increase the result count limit to get more features before sampling
            if 'resultRecordCount' not in params:
                params['resultRecordCount'] = '5000'  # Get more, then sample
            
            upstream = requests.get(endpoint, params=params)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")}
            )
    else:
        # layer metadata
        upstream = requests.get(ARCGIS_URL, params=request.args)
    
    # If this is a feature query response, apply smart sampling
    if is_query and upstream.status_code == 200:
        # Check if we should apply sampling (only for viewport queries)
        should_sample = False
        viewport_bounds = None
        zoom_level = 10  # default zoom
        
        if request.method == "GET":
            # Sample if spatial bounds were provided
            should_sample = any(param in request.args for param in ['xmin', 'ymin', 'xmax', 'ymax'])
            
            if should_sample:
                # Extract viewport bounds and zoom level
                viewport_bounds = {
                    'west': float(request.args.get('xmin', 0)),
                    'south': float(request.args.get('ymin', 0)),
                    'east': float(request.args.get('xmax', 0)),
                    'north': float(request.args.get('ymax', 0))
                }
                zoom_level = int(request.args.get('zoom', 10))
                
                # Add more debug info
                print(f"DEBUG: Viewport bounds: {viewport_bounds}")
                print(f"DEBUG: Zoom level: {zoom_level}")
                print(f"DEBUG: Should sample: {should_sample}")
        
        if should_sample:
            # Apply smart sampling based on zoom and viewport
            modified_content = limit_features_randomly(
                upstream.content.decode('utf-8'), 
                viewport_bounds=viewport_bounds,
                zoom_level=zoom_level,
                max_features=1000
            )
            return Response(
                modified_content,
                status=upstream.status_code,
                content_type="application/json"
            )
    
    # Return original response for metadata or non-sampled queries
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

# 3. Add a new endpoint for viewport-based queries
@app.route("/api/landmarks/viewport", methods=["POST"])
def get_viewport_landmarks():
    """Get landmarks for specific viewport with automatic random sampling"""
    try:
        data = request.get_json()
        bounds = data.get('bounds', {})
        
        if not all(k in bounds for k in ['west', 'south', 'east', 'north']):
            return jsonify({"error": "Missing viewport bounds"}), 400
        
        # Create spatial query parameters
        envelope = f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
        
        params = {
            'where': '1=1',  # Get all features in area
            'geometry': envelope,
            'geometryType': 'esriGeometryEnvelope',
            'spatialRel': 'esriSpatialRelIntersects',
            'inSR': '4326',
            'outSR': '4326',
            'f': 'geojson',
            'outFields': '*',
            'resultRecordCount': '5000'  # Get more features before sampling
        }
        
        # Add category filter if provided
        if 'category' in data and data['category']:
            params['where'] = f"main_category = '{data['category']}'"
        
        # Query ArcGIS
        response = requests.get(f"{ARCGIS_URL}/query", params=params)
        
        if response.status_code == 200:
            # Get zoom level from request
            zoom_level = data.get('zoom', 12)
            
            # Create viewport bounds for consistent sampling
            viewport_bounds = bounds
            
            # Apply smart sampling
            sampled_content = limit_features_randomly(
                response.content.decode('utf-8'), 
                viewport_bounds=viewport_bounds,
                zoom_level=zoom_level,
                max_features=1000
            )
            return Response(sampled_content, content_type="application/json")
        else:
            return jsonify({"error": "ArcGIS query failed"}), response.status_code
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)