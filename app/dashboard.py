import math
import folium
import locale
import requests
import osmnx as ox
import pandas as pd
import streamlit as st
import geopandas as gpd
from geopy.geocoders import Nominatim
from shapely.ops import nearest_points
from shapely.geometry import Point, LineString
from shapely.geometry import LineString, Point


# Caching functions for faster loading
@st.cache_data
def get_sidewalks(location_name):
    # Find streets inside the district
    graph = ox.graph_from_place(location_name, network_type="all_private")
    streets_gdf = ox.graph_to_gdfs(graph, nodes=False, edges=True)

    # Extract sidewalks from the streets
    streets_gdf = streets_gdf[
        streets_gdf["highway"].apply(
            lambda x: ["footway", "path", "pedestrian", "living_street"].__contains__(x)
        )
    ]
    return streets_gdf


@st.cache_data
def get_benches(location_name, _district, benches_file=None):
    # Find benches inside the district
    benches_gdf = ox.features_from_place(location_name, tags={"amenity": "bench"})

    # Parse benches file
    if benches_file is not None:
        if benches_file.name.endswith(".csv"):
            imported_benches = pd.read_csv(benches_file, sep=";")
        elif benches_file.name.endswith(".xlsx"):
            imported_benches = pd.read_excel(benches_file)
        else:
            st.warning("Invalid file format. Only CSV and XLSX files are supported.")
            return benches_gdf
        if "lon" in imported_benches.columns and "lat" in imported_benches.columns:
            imported_benches["geometry"] = imported_benches.apply(
                lambda row: Point(row["lon"], row["lat"]), axis=1
            )
            benches_gdf = pd.concat([benches_gdf, imported_benches])
            benches_gdf = benches_gdf[benches_gdf.within(district.geometry[0])]
        else:
            st.warning(
                "The uploaded file does not contain `lon` and `lat` columns. Ignoring..."
            )

    return benches_gdf

def segment_streets(streets_gdf, max_length):
    new_geometries = []
    expanded_data = {column: [] for column in streets_gdf.columns if column != 'geometry'}
    
    for _, row in streets_gdf.iterrows():
        segments = segment_line(row.geometry, max_length)
        new_geometries.extend(segments)
        for column in expanded_data:
            expanded_data[column].extend([row[column]] * len(segments))

    new_streets_gdf = gpd.GeoDataFrame(expanded_data, geometry=new_geometries, crs=streets_gdf.crs)
    return new_streets_gdf


def segment_line(line, max_length):
    num_segments = math.ceil(line.length / max_length)
    segments = []

    for i in range(num_segments):
        start_dist = i * max_length
        end_dist = min(start_dist + max_length, line.length)
        if end_dist < line.length:
            segment = LineString([line.interpolate(start_dist), line.interpolate(end_dist)])
        else:
            segment = LineString([line.interpolate(start_dist), line.coords[-1]])
        segments.append(segment)

    return segments

@st.cache_data
def calculate_distances(_streets_gdf, _benches_gdf, location_name, benches_file, method='geoseries', max_street_length=50):
    # Convert max_street_length from meters to degrees
    max_length_degrees = max_street_length / 111320
    new_streets_gdf = segment_streets(_streets_gdf, max_length_degrees)

    if method == 'geoseries':
        # Convert all bench geometries to points (if they are not already points)
        _benches_gdf['geometry'] = _benches_gdf['geometry'].apply(lambda geom: geom.centroid if not isinstance(geom, Point) else geom)

        # Calculate the minimum distance from each street segment to any bench
        new_streets_gdf['distance_to_bench'] = new_streets_gdf['geometry'].apply(lambda x: _benches_gdf.distance(x).min())

    elif method == 'projection':
        # Helper function to get coordinates
        def get_coords(geometry):
            if isinstance(geometry, Point):
                return geometry
            else:
                return geometry.centroid

        # Point Projection Approach
        def distance_projection(row, benches_line):
            street_point = row.geometry.centroid
            nearest_bench = nearest_points(street_point, benches_line)[1]
            return street_point.distance(nearest_bench)

        benches_coords = _benches_gdf.geometry.apply(get_coords)
        benches_line = benches_coords.unary_union
        new_streets_gdf['distance_to_bench'] = new_streets_gdf.apply(lambda row: distance_projection(row, benches_line), axis=1)

    else:
        raise ValueError("Invalid method specified")

    return new_streets_gdf


def get_districts(city_name):
    # Replace spaces with underscore in city name for URL formatting
    city_name_formatted = city_name.replace(" ", "_")

    # Overpass API Query
    # This query looks for nodes tagged as 'place=suburb' within the city
    query = f"""
    [out:json];
    area[name="{city_name}"]->.searchArea;
    (
      rel(area.searchArea)["admin_level"="9"];
    );
    out body;
    """

    # URL of the Overpass API
    url = "http://overpass-api.de/api/interpreter"

    # Send request to Overpass API
    response = requests.get(url, params={"data": query})
    data = response.json()

    # Extract district names
    districts = [element["tags"]["name"] for element in data["elements"]]

    # Sort districts alphabetically
    districts.sort(key=locale.strxfrm)

    return districts


# Set page configuration
st.set_page_config(layout="wide", page_title="Street Highlighter", page_icon="🗺️")
st.markdown(  # hardcoded style for the sidebar and the map
    """<style>
        .st-emotion-cache-10oheav.eczjsme4 {
            padding-top: 16px;
        }
        .st-emotion-cache-z5fcl4.ea3mdgi4 {
            padding-top: 32px;
        }
        </style>
        """,
    unsafe_allow_html=True,
)
locale.setlocale(locale.LC_COLLATE, "pl_PL.UTF-8")


# Sidebar for user input
with st.sidebar:
    city = st.selectbox("Select a city:", ["Poznań", "Warszawa", "Hasselt", "Żnin"])
    districts = get_districts(city)
    district_name = st.selectbox(
        "Select a district:",
        [""] + districts + ["ALL"],
    )
    if district_name == "":
        st.warning("Please select a district.")
        st.stop()
    elif district_name == "ALL":
        location_name = f"{city}"
    else:
        location_name = f"{district_name}, {city}"

    st.write("# Map options:")
    show_benches = st.checkbox("Show benches", value=True)
    st.write("\n")

    col1, col2 = st.columns(2)
    with col1:
        show_good_streets = st.checkbox("Show good streets", value=True)
    with col2:
        good_street_color = st.color_picker("Good street color", value="#009900")
    col3, col4 = st.columns(2)
    with col3:
        show_okay_streets = st.checkbox("Show okay streets", value=True)
    with col4:
        okay_street_color = st.color_picker("Okay street color", value="#FFA500")
    col5, col6 = st.columns(2)
    with col5:
        show_bad_streets = st.checkbox("Show bad streets", value=True)
    with col6:
        bad_street_color = st.color_picker("Bad street color", value="#FF0000")
    col7, col8 = st.columns(2)
    with col7:
        good_street_value = st.slider(
            "Good street distance", min_value=0, max_value=300, value=50
        )
    with col8:
        okay_street_value = st.slider(
            "Okay street distance", min_value=0, max_value=300, value=150
        )

    st.write("\n")
    benches_file = st.file_uploader("Upload benches file", type=["csv", "xlsx"])
    st.write(
        "ℹ️ The file should contain `lon` and `lat` columns with coordinates of benches."
    )


# Initialize progress bar
step_text = st.empty()
progress_bar = st.progress(0)
step_text.text("Initializing geolocator...")

# Initialize geolocator
geolocator = Nominatim(user_agent="street-highlighter")
location = geolocator.geocode(location_name)

progress_bar.progress(5)
step_text.text("Creating map...")

# Create Folium map
m = folium.Map(
    location=[location.latitude, location.longitude],
    zoom_start=15,
    max_zoom=20,
    tiles="cartodbpositron",
    use_container_width=True,
)

progress_bar.progress(10)
step_text.text("Adding district boundaries...")

# Get the district boundaries and add to the map
district = ox.geocode_to_gdf(location_name)
folium.GeoJson(district).add_to(m)

progress_bar.progress(15)
step_text.text("Finding sidewalks...")

# Find streets inside the district
streets_gdf = get_sidewalks(location_name)

progress_bar.progress(25)
step_text.text("Finding benches...")

# Find benches inside the district
benches_gdf = get_benches(location_name, district, benches_file)

progress_bar.progress(30)
if show_benches:
    step_text.text("Drawing benches...")

    # Draw benches on the map
    for bench in benches_gdf.iterrows():
        icon = folium.features.CustomIcon(
            "https://cdn-icons-png.flaticon.com/256/2256/2256995.png",
            icon_size=(15, 15),
        )
        bench_coords = bench[1].geometry.centroid.coords[0]
        if bench[1]["amenity"] != "bench":
            tooltip = "Imported bench"
        else:
            tooltip = None
        folium.Marker(
            location=[bench_coords[1], bench_coords[0]], icon=icon, tooltip=tooltip
        ).add_to(m)

progress_bar.progress(35)
step_text.text("Calculating distances...")

# Calculate distance of each street to the nearest bench
street_distances = calculate_distances(streets_gdf, benches_gdf, location_name, benches_file=benches_file)

progress_bar.progress(45)
step_text.text("Filtering streets...")

# Filter streets based on distance to benches
streets_with_benches = street_distances[
    street_distances["distance_to_bench"] <= 50 / 111320
]  # 50 meters in degrees

progress_bar.progress(50)
step_text.text("Drawing streets...")

total_streets = len(street_distances)
for index, row in enumerate(street_distances.iterrows()):
    if show_good_streets and row[1]["distance_to_bench"] <= good_street_value / 111320:
        color = good_street_color
    elif (
        show_okay_streets and row[1]["distance_to_bench"] <= okay_street_value / 111320
    ):
        color = okay_street_color
    elif show_bad_streets and row[1]["distance_to_bench"] > okay_street_value / 111320:
        color = bad_street_color
    else:
        continue
    line_points = [(lat, lon) for lon, lat in row[1].geometry.coords]
    folium.PolyLine(
        locations=line_points,
        color=color,
        weight=4,
        tooltip=f" {index} Distance to nearest bench: {round(row[1]['distance_to_bench']*111320, 2)} meters, type: {row[1]['highway']}",
    ).add_to(m)

    progress_bar.progress(0.5 + (index + 1) / total_streets / 2)

# Reset progress bar
progress_bar.empty()
step_text.empty()

# Load and configure map
st.components.v1.html(m._repr_html_(), height=4320, scrolling=False)
st.markdown(
    """<style>
        .st-emotion-cache-z5fcl4.ea3mdgi4 {
            overflow: hidden;
        }
        """,
    unsafe_allow_html=True,
)