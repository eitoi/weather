"""Static configuration: domain, levels, fields, places."""

# Wasatch Front / Wasatch Back, Mount Wire (north) to Mount Nebo (south),
# Salt Lake & Utah valleys (west) to Park City / Heber side (east).
BBOX = dict(south=39.68, north=40.95, west=-112.08, east=-111.30)
# Slightly larger box for NOMADS server-side subsetting of comparison models.
BBOX_PAD = dict(south=39.4, north=41.2, west=-112.5, east=-110.9)

HRRR_BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin"
USER_AGENT = "wasatch-wx/1.0 (personal forecast app)"

# Every 25 mb level from just below the valley floors (~860 mb) to the jet stream.
LEVELS = list(range(900, 199, -25))  # 900 .. 200 mb (29 levels)
UPPER_VARS = ["HGT", "TMP", "DPT", "RH", "UGRD", "VGRD", "VVEL"]

# Fixed precision per variable so int16 storage is exact to the shown digits.
# value = int16 * scale   (units already converted at ingest, see UNITS)
SCALE = {
    "HGT": 1.0, "TMP": 0.01, "DPT": 0.01, "RH": 0.01, "UGRD": 0.01, "VGRD": 0.01, "VVEL": 0.001,
}
UNITS = {"HGT": "m", "TMP": "C", "DPT": "C", "RH": "%", "UGRD": "m/s", "VGRD": "m/s", "VVEL": "Pa/s"}

# Surface / single-level HRRR fields from wrfsfc files.
# key: (idx var, idx level, units, scale, convert)
#   convert: "K2C" Kelvin->C, "m2cm", "Pa2hPa", "m2km" or None
SFC_FIELDS = {
    "T2":     ("TMP", "2 m above ground", "C", 0.01, "K2C"),
    "D2":     ("DPT", "2 m above ground", "C", 0.01, "K2C"),
    "RH2":    ("RH", "2 m above ground", "%", 0.01, None),
    "U10":    ("UGRD", "10 m above ground", "m/s", 0.01, None),
    "V10":    ("VGRD", "10 m above ground", "m/s", 0.01, None),
    "WMAX10": ("WIND", "10 m above ground", "m/s", 0.01, None),
    "GUST":   ("GUST", "surface", "m/s", 0.01, None),
    "U80":    ("UGRD", "80 m above ground", "m/s", 0.01, None),
    "V80":    ("VGRD", "80 m above ground", "m/s", 0.01, None),
    "APCP1":  ("APCP", "surface", "mm", 0.01, None),      # 1-hour accumulation (special-cased)
    "APCPT":  ("APCP", "surface", "mm", 0.1, None),       # run-total accumulation (special-cased)
    "ASNOW":  ("ASNOW", "surface", "cm", 0.01, "m2cm"),   # run-total snowfall
    "PRATE":  ("PRATE", "surface", "mm/h", 0.01, "s2h"),
    "CPOFP":  ("CPOFP", "surface", "%", 0.1, None),
    "CSNOW":  ("CSNOW", "surface", "", 1, None),
    "CRAIN":  ("CRAIN", "surface", "", 1, None),
    "CFRZR":  ("CFRZR", "surface", "", 1, None),
    "CICEP":  ("CICEP", "surface", "", 1, None),
    "TCDC":   ("TCDC", "entire atmosphere", "%", 0.1, None),
    "LCDC":   ("LCDC", "low cloud layer", "%", 0.1, None),
    "MCDC":   ("MCDC", "middle cloud layer", "%", 0.1, None),
    "HCDC":   ("HCDC", "high cloud layer", "%", 0.1, None),
    "CEIL":   ("HGT", "cloud ceiling", "m", 1, None),
    "CBASE":  ("HGT", "cloud base", "m", 1, None),
    "CTOP":   ("HGT", "cloud top", "m", 1, None),
    "VIS":    ("VIS", "surface", "km", 0.01, "m2km"),
    "HPBL":   ("HPBL", "surface", "m", 1, None),
    "CAPE":   ("CAPE", "surface", "J/kg", 1, None),
    "CIN":    ("CIN", "surface", "J/kg", 1, None),
    "LFTX":   ("LFTX", "500-1000 mb", "C", 0.01, None),
    "PWAT":   ("PWAT", "entire atmosphere (considered as a single layer)", "mm", 0.01, None),
    "FRZLVL": ("HGT", "0C isotherm", "m", 1, None),
    "REFC":   ("REFC", "entire atmosphere", "dBZ", 0.01, None),
    "LTNG":   ("LTNG", "entire atmosphere", "fl/km2/5min", 0.001, None),
    "DSWRF":  ("DSWRF", "surface", "W/m2", 0.1, None),
    "PSFC":   ("PRES", "surface", "hPa", 0.1, "Pa2hPa"),
    "MSLP":   ("MSLMA", "mean sea level", "hPa", 0.1, "Pa2hPa"),
    "SNOD":   ("SNOD", "surface", "cm", 0.1, "m2cm"),
    "TERRAIN": ("HGT", "surface", "m", 1, None),
}

# Fields fetched from comparison models (NAM 3 km nest, RAP 13 km, earlier HRRR runs).
# key -> (var, level as in idx / NOMADS lev_ name, convert)
CMP_FIELDS = {
    "T2":   ("TMP", "2 m above ground", "K2C"),
    "U10":  ("UGRD", "10 m above ground", None),
    "V10":  ("VGRD", "10 m above ground", None),
    "GUST": ("GUST", "surface", None),
    "APCP": ("APCP", "surface", None),
    "T700": ("TMP", "700 mb", "K2C"),
    "U700": ("UGRD", "700 mb", None),
    "V700": ("VGRD", "700 mb", None),
}

# Points of interest (lat, lon). Also used as long-range forecast zones (zone=True).
PLACES = [
    {"id": "wire", "name": "Mount Wire", "lat": 40.7727, "lon": -111.8172},
    {"id": "grandview", "name": "Grandview Peak", "lat": 40.8455, "lon": -111.7522},
    {"id": "slc", "name": "Salt Lake City (KSLC)", "lat": 40.7884, "lon": -111.9778, "zone": True},
    {"id": "olympus", "name": "Mount Olympus", "lat": 40.6560, "lon": -111.7704},
    {"id": "alta", "name": "Alta", "lat": 40.5884, "lon": -111.6386, "zone": True},
    {"id": "hidden", "name": "Hidden Peak (Snowbird)", "lat": 40.5617, "lon": -111.6455},
    {"id": "brighton", "name": "Brighton", "lat": 40.5980, "lon": -111.5832},
    {"id": "guardsman", "name": "Guardsman Pass", "lat": 40.6080, "lon": -111.5530},
    {"id": "parkcity", "name": "Park City", "lat": 40.6461, "lon": -111.4980, "zone": True},
    {"id": "lonepeak", "name": "Lone Peak", "lat": 40.5266, "lon": -111.7557},
    {"id": "potm_n", "name": "Point of the Mountain (North)", "lat": 40.4730, "lon": -111.8890},
    {"id": "potm_s", "name": "Point of the Mountain (South)", "lat": 40.4535, "lon": -111.9025},
    {"id": "timp", "name": "Mount Timpanogos", "lat": 40.3908, "lon": -111.6458, "zone": True},
    {"id": "sundance", "name": "Sundance", "lat": 40.3920, "lon": -111.5880},
    {"id": "provo", "name": "Provo", "lat": 40.2338, "lon": -111.6585, "zone": True},
    {"id": "spanishfork", "name": "Spanish Fork Peak", "lat": 40.0960, "lon": -111.5640},
    {"id": "nebo", "name": "Mount Nebo", "lat": 39.8219, "lon": -111.7603, "zone": True},
]

TIMEZONE = "America/Denver"
NWS_OFFICE = "SLC"
