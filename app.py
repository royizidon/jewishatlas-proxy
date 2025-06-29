from flask import Flask, request, Response, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv
import json
import random
import math
from collections import defaultdict

# Load environment and setup Flask
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
app = Flask(__name__)
CORS(app)

def create_clusters(features, zoom_level):
    """Create clusters from features based on geographic proximity and zoom level"""
    
    print(f"DEBUG: create_clusters called with zoom={zoom_level}, features={len(features)}")
    
    # At zoom 12+, return individual points with is_cluster=False
    if zoom_level >= 12:
        print("DEBUG: High zoom (12+) - returning individual points")
        for feature in features:
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['is_cluster'] = False
            feature['properties']['cluster_count'] = 1
        return features
    
    # At lower zoom levels, create clusters
    print("DEBUG: Low zoom (<12) - creating clusters")
    
    # Dynamic cluster distance based on zoom level
    # Lower zoom = larger clusters, higher zoom = smaller clusters
    if zoom_level <= 6:
        cluster_distance_km = 100  # 100km at very low zoom
    elif zoom_level <= 8:
        cluster_distance_km = 50   # 50km at low zoom
    elif zoom_level <= 10:
        cluster_distance_km = 20   # 20km at medium zoom
    else:
        cluster_distance_km = 10   # 10km at medium-high zoom
    
    # Convert km to degrees (rough approximation: 1 degree ≈ 111km)
    cluster_radius_degrees = cluster_distance_km / 111.0
    print(f"DEBUG: Zoom {zoom_level} - using cluster radius: {cluster_distance_km}km ({cluster_radius_degrees:.4f} degrees)")
    
    clusters = []
    unclustered = features[:]
    cluster_id = 0
    
    while unclustered:
        # Start a new cluster with the first unclustered point
        seed_feature = unclustered.pop(0)
        seed_coords = seed_feature.get('geometry', {}).get('coordinates', [0, 0])
        
        if not seed_coords or len(seed_coords) < 2:
            continue
            
        cluster_features = [seed_feature]
        cluster_center_lon = seed_coords[0] 
        cluster_center_lat = seed_coords[1]
        
        # Find all features within cluster radius
        remaining = []
        for feature in unclustered:
            coords = feature.get('geometry', {}).get('coordinates', [0, 0])
            if len(coords) >= 2:
                # Calculate distance between points
                distance = math.sqrt(
                    (coords[0] - cluster_center_lon) ** 2 + 
                    (coords[1] - cluster_center_lat) ** 2
                )
                
                if distance <= cluster_radius_degrees:
                    cluster_features.append(feature)
                    # Update cluster center (centroid of all points)
                    cluster_center_lon = sum(f.get('geometry', {}).get('coordinates', [0, 0])[0] 
                                           for f in cluster_features) / len(cluster_features)
                    cluster_center_lat = sum(f.get('geometry', {}).get('coordinates', [0, 0])[1] 
                                           for f in cluster_features) / len(cluster_features)
                else:
                    remaining.append(feature)
            else:
                remaining.append(feature)
        
        unclustered = remaining
        
        if len(cluster_features) == 1:
            # Single point - mark as individual feature
            feature = cluster_features[0]
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['is_cluster'] = False
            feature['properties']['cluster_count'] = 1
            clusters.append(feature)
        else:
            # Create cluster feature (2+ points)
            categories = defaultdict(int)
            total_count = len(cluster_features)
            
            # Count categories in cluster
            for feature in cluster_features:
                category = feature.get('properties', {}).get('main_category', 'Unknown')
                categories[category] += 1
            
            # Find dominant category
            dominant_category = max(categories.items(), key=lambda x: x[1])[0] if categories else 'Unknown'
            
            # Create cluster feature with proper structure
            cluster_feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [cluster_center_lon, cluster_center_lat]
                },
                'properties': {
                    'OBJECTID': f"cluster_{cluster_id}",
                    'main_category': dominant_category,
                    'cluster_count': total_count,
                    'is_cluster': True,
                    'categories': dict(categories),
                    'Name': f"Cluster of {total_count} Jewish Sites",
                    'eng_name': f"Cluster ({total_count})",
                    'Address': f"{total_count} sites in this area"
                }
            }
            clusters.append(cluster_feature)
            cluster_id += 1
    
    cluster_count = len([c for c in clusters if c.get('properties', {}).get('is_cluster', False)])
    point_count = len([c for c in clusters if not c.get('properties', {}).get('is_cluster', False)])
    print(f"DEBUG: Created {cluster_count} clusters and {point_count} individual points")
    
    return clusters

def process_features(response_data, zoom_level=10, viewport_bounds=None):
    """Process features with clustering based on zoom level"""
    try:
        data = json.loads(response_data)
        
        if 'features' not in data:
            print("DEBUG: No features in response")
            return response_data
        
        features = data['features']
        original_count = len(features)
        
        print(f"DEBUG: Processing {original_count} features at zoom {zoom_level}")
        
        if original_count == 0:
            print("DEBUG: No features to process")
            return response_data
        
        # Create consistent seed for viewport if provided (for reproducible sampling)
        if viewport_bounds:
            seed_str = f"{viewport_bounds.get('west', 0):.3f},{viewport_bounds.get('south', 0):.3f},{viewport_bounds.get('east', 0):.3f},{viewport_bounds.get('north', 0):.3f}"
            seed = hash(seed_str) % (2**32)
            random.seed(seed)
        
        # Sample features if too many (to prevent performance issues)
        max_features_for_processing = 2000
        if len(features) > max_features_for_processing:
            print(f"DEBUG: Sampling {max_features_for_processing} from {len(features)} features")
            
            # Prioritize Featured sites
            featured = [f for f in features if f.get('properties', {}).get('main_category') == 'Featured']
            others = [f for f in features if f.get('properties', {}).get('main_category') != 'Featured']
            
            remaining_slots = max_features_for_processing - len(featured)
            if remaining_slots > 0 and others:
                if len(others) <= remaining_slots:
                    sampled_others = others
                else:
                    sampled_others = random.sample(others, remaining_slots)
                features = featured + sampled_others
            else:
                features = featured[:max_features_for_processing]
        
        # Apply clustering logic
        processed_features = create_clusters(features, zoom_level)
        
        # Update the response
        data['features'] = processed_features
        
        # Add metadata for debugging
        if 'metadata' not in data:
            data['metadata'] = {}
        
        cluster_count = len([f for f in processed_features if f.get('properties', {}).get('is_cluster', False)])
        point_count = len([f for f in processed_features if not f.get('properties', {}).get('is_cluster', False)])
        
        data['metadata'].update({
            'processed': True,
            'original_count': original_count,
            'returned_count': len(processed_features),
            'cluster_count': cluster_count,
            'point_count': point_count, 
            'zoom_level': zoom_level,
            'display_mode': 'clusters' if zoom_level < 12 else 'points',
            'viewport_bounds': viewport_bounds
        })
        
        print(f"DEBUG: Final result - {cluster_count} clusters, {point_count} points")
        return json.dumps(data)
        
    except Exception as e:
        print(f"ERROR: Processing failed: {e}")
        import traceback
        traceback.print_exc()
        return response_data

# Main proxy endpoint
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """Proxy requests to ArcGIS with smart clustering"""
    
    print(f"DEBUG: Request to {request.path} with method {request.method}")
    
    # Determine if this is a feature query
    is_query = request.method == "POST" or "where" in request.args
    
    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        
        if request.method == "GET":
            params = dict(request.args)
            zoom_level = int(params.get('zoom', 10))
            
            print(f"DEBUG: GET query with zoom={zoom_level}")
            print(f"DEBUG: Request params: {list(params.keys())}")
            
            # Handle spatial filtering
            viewport_bounds = None
            if all(param in params for param in ['xmin', 'ymin', 'xmax', 'ymax']):
                # Extract bounds
                xmin = float(params['xmin'])
                ymin = float(params['ymin'])
                xmax = float(params['xmax'])
                ymax = float(params['ymax'])
                
                print(f"DEBUG: Spatial bounds - xmin:{xmin}, ymin:{ymin}, xmax:{xmax}, ymax:{ymax}")
                
                # Create envelope geometry for ArcGIS
                envelope = f"{xmin},{ymin},{xmax},{ymax}"
                params['geometry'] = envelope
                params['geometryType'] = 'esriGeometryEnvelope'
                params['spatialRel'] = 'esriSpatialRelIntersects'
                params['inSR'] = '4326'
                params['outSR'] = '4326'
                
                # Store viewport bounds for processing
                viewport_bounds = {
                    'west': xmin, 'south': ymin, 
                    'east': xmax, 'north': ymax
                }
                
                # Remove individual bound parameters (ArcGIS doesn't need them)
                for bound_param in ['xmin', 'ymin', 'xmax', 'ymax']:
                    params.pop(bound_param, None)
            
            # Remove zoom parameter (ArcGIS doesn't understand it)
            params.pop('zoom', None)
            
            # Ensure we get enough features for clustering
            if 'resultRecordCount' not in params:
                params['resultRecordCount'] = '5000'
            
            # Ensure GeoJSON format
            params['f'] = 'geojson'
            
            # Ensure we get all necessary fields
            if 'outFields' not in params:
                params['outFields'] = '*'
            
            print(f"DEBUG: Final ArcGIS params: {list(params.keys())}")
            
            # Make the request to ArcGIS
            try:
                upstream = requests.get(endpoint, params=params, timeout=30)
                print(f"DEBUG: ArcGIS response status: {upstream.status_code}")
            except Exception as e:
                print(f"ERROR: ArcGIS request failed: {e}")
                return jsonify({"error": "ArcGIS request failed"}), 500
            
        else:
            # POST request
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")},
                timeout=30
            )
            zoom_level = 10  # Default for POST
            viewport_bounds = None
    
    else:
        # Metadata request
        print("DEBUG: Metadata request")
        upstream = requests.get(ARCGIS_URL, params=request.args, timeout=30)
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )
    
    # Process the response if it's a successful feature query
    if upstream.status_code == 200 and is_query:
        try:
            response_text = upstream.content.decode('utf-8')
            print(f"DEBUG: ArcGIS returned {len(response_text)} characters")
            
            # Quick check if response contains features
            if '"features"' in response_text:
                # Process features with clustering
                processed_content = process_features(
                    response_text,
                    zoom_level=zoom_level,
                    viewport_bounds=viewport_bounds
                )
                
                return Response(
                    processed_content,
                    status=200,
                    content_type="application/json",
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                        'Access-Control-Allow-Headers': 'Content-Type'
                    }
                )
            else:
                print("DEBUG: No features in ArcGIS response")
                return Response(
                    response_text,
                    status=200,
                    content_type="application/json"
                )
            
        except Exception as e:
            print(f"ERROR: Failed to process response: {e}")
            import traceback
            traceback.print_exc()
            # Return original response on error
            return Response(
                upstream.content,
                status=upstream.status_code,
                content_type=upstream.headers.get("Content-Type", "application/json")
            )
    else:
        print(f"DEBUG: Non-successful or non-query response: {upstream.status_code}")
    
    # Return original response for non-successful or non-query requests
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

@app.route("/health", methods=["GET"])
def health_check():
    """Simple health check endpoint"""
    return jsonify({"status": "healthy", "arcgis_url": ARCGIS_URL}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting server on port {port}")
    print(f"ArcGIS URL: {ARCGIS_URL}")
    app.run(host="0.0.0.0", port=port, debug=True)