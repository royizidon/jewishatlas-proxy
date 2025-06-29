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

def create_clusters(features, zoom_level, cluster_distance=50):
    """Create clusters from features based on geographic proximity"""
    
    # Debug logging
    print(f"DEBUG: create_clusters called with zoom={zoom_level}, features={len(features)}")
    
    # At zoom 12+, return individual points with is_cluster=False
    if zoom_level >= 12:
        print("DEBUG: High zoom - returning individual points")
        for feature in features:
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['is_cluster'] = False
            feature['properties']['cluster_count'] = 1
        return features
    
    # At lower zoom levels, create clusters
    print("DEBUG: Low zoom - creating clusters")
    clusters = []
    unclustered = features[:]
    
    # Calculate cluster distance based on zoom level
    # More aggressive clustering at lower zoom levels
    cluster_radius_degrees = cluster_distance / (111000 * (2 ** (zoom_level - 1)))
    print(f"DEBUG: Cluster radius: {cluster_radius_degrees} degrees")
    
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
                # Calculate simple distance
                distance = math.sqrt(
                    (coords[0] - cluster_center_lon) ** 2 + 
                    (coords[1] - cluster_center_lat) ** 2
                )
                
                if distance <= cluster_radius_degrees:
                    cluster_features.append(feature)
                    # Update cluster center (average of all points)
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
            # Create cluster feature
            categories = defaultdict(int)
            total_count = len(cluster_features)
            
            # Count categories in cluster
            for feature in cluster_features:
                category = feature.get('properties', {}).get('main_category', 'Unknown')
                categories[category] += 1
            
            # Find dominant category
            dominant_category = max(categories.items(), key=lambda x: x[1])[0] if categories else 'Unknown'
            
            # Create cluster feature
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
    
    print(f"DEBUG: Created {len([c for c in clusters if c.get('properties', {}).get('is_cluster')])} clusters and {len([c for c in clusters if not c.get('properties', {}).get('is_cluster')])} individual points")
    return clusters

def process_features(response_data, zoom_level=10, viewport_bounds=None):
    """Process features with clustering based on zoom level"""
    try:
        data = json.loads(response_data)
        
        if 'features' not in data:
            return response_data
        
        features = data['features']
        original_count = len(features)
        
        print(f"DEBUG: Processing {original_count} features at zoom {zoom_level}")
        
        # Create consistent seed for viewport if provided
        if viewport_bounds:
            seed_str = f"{viewport_bounds.get('west', 0):.3f},{viewport_bounds.get('south', 0):.3f},{viewport_bounds.get('east', 0):.3f},{viewport_bounds.get('north', 0):.3f}"
            seed = hash(seed_str) % (2**32)
            random.seed(seed)
        
        # Sample features if too many (to prevent overload)
        max_features_for_processing = 3000
        if len(features) > max_features_for_processing:
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
                features = featured
        
        # Apply clustering logic
        processed_features = create_clusters(features, zoom_level)
        
        # Update the response
        data['features'] = processed_features
        
        # Add metadata
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
            'display_mode': 'clusters' if zoom_level < 12 else 'points'
        })
        
        print(f"DEBUG: Returning {cluster_count} clusters and {point_count} points")
        return json.dumps(data)
        
    except Exception as e:
        print(f"ERROR: Processing failed: {e}")
        return response_data

# Main proxy endpoint
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """Proxy requests to ArcGIS with smart clustering"""
    
    # Determine if this is a feature query
    is_query = request.method == "POST" or "where" in request.args
    
    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        
        if request.method == "GET":
            params = dict(request.args)
            zoom_level = int(params.get('zoom', 10))
            
            print(f"DEBUG: Query with zoom={zoom_level}, params={list(params.keys())}")
            
            # Handle spatial filtering
            if all(param in params for param in ['xmin', 'ymin', 'xmax', 'ymax']):
                # Extract bounds
                xmin = float(params['xmin'])
                ymin = float(params['ymin'])
                xmax = float(params['xmax'])
                ymax = float(params['ymax'])
                
                # Create envelope geometry
                envelope = f"{xmin},{ymin},{xmax},{ymax}"
                params['geometry'] = envelope
                params['geometryType'] = 'esriGeometryEnvelope'
                params['spatialRel'] = 'esriSpatialRelIntersects'
                params['inSR'] = '4326'  # Input spatial reference
                params['outSR'] = '4326'  # Output spatial reference
                
                # Store viewport bounds for processing
                viewport_bounds = {
                    'west': xmin, 'south': ymin, 
                    'east': xmax, 'north': ymax
                }
                
                # Remove individual bound parameters
                for bound_param in ['xmin', 'ymin', 'xmax', 'ymax']:
                    params.pop(bound_param, None)
            else:
                viewport_bounds = None
            
            # Ensure we get enough features for clustering
            if 'resultRecordCount' not in params:
                params['resultRecordCount'] = '5000'
            
            # Ensure GeoJSON format
            params['f'] = 'geojson'
            
            # Make the request
            upstream = requests.get(endpoint, params=params, timeout=30)
            
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
        upstream = requests.get(ARCGIS_URL, params=request.args, timeout=30)
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )
    
    # Process the response if it's a successful feature query
    if upstream.status_code == 200 and is_query:
        try:
            # Process features with clustering
            processed_content = process_features(
                upstream.content.decode('utf-8'),
                zoom_level=zoom_level,
                viewport_bounds=viewport_bounds
            )
            
            return Response(
                processed_content,
                status=200,
                content_type="application/json"
            )
            
        except Exception as e:
            print(f"ERROR: Failed to process response: {e}")
            # Return original response on error
            return Response(
                upstream.content,
                status=upstream.status_code,
                content_type=upstream.headers.get("Content-Type", "application/json")
            )
    
    # Return original response for non-successful or non-query requests
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)