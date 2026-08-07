"""A small Wellington gazetteer — place name to approximate coordinates.

Approximate on purpose. These are suburb and landmark centroids, good enough to
say "somewhere around Island Bay" and no better. A point on a map reads as a
precise claim, so `geolocate` records the kind of match and the interface is
expected to show the uncertainty rather than a confident pin.

Coordinates are rounded to four decimals (~10 m), which is already far more
precision than the method deserves.

Swapping this for the WCC GIS suburb layer would be a straightforward upgrade:
the shape of a suburb polygon is a more honest answer than its centroid.
"""

# name, lat, lon, kind, aliases
ENTRIES: list[tuple[str, float, float, str, tuple[str, ...]]] = [
    # --- central ---
    ("Wellington Central", -41.2866, 174.7756, "suburb", ("wellington cbd", "the cbd")),
    ("Te Aro", -41.2940, 174.7762, "suburb", ()),
    ("Thorndon", -41.2740, 174.7780, "suburb", ()),
    ("Pipitea", -41.2770, 174.7810, "suburb", ()),
    ("Mount Cook", -41.3010, 174.7740, "suburb", ("mt cook",)),
    ("Mount Victoria", -41.2950, 174.7880, "suburb", ("mt victoria", "mt vic")),
    ("Aro Valley", -41.2940, 174.7650, "suburb", ("aro street",)),
    ("Kelburn", -41.2870, 174.7650, "suburb", ()),
    ("Northland", -41.2860, 174.7570, "suburb", ()),
    ("Highbury", -41.2930, 174.7590, "suburb", ()),
    ("Oriental Bay", -41.2910, 174.7900, "suburb", ("oriental parade",)),
    ("Roseneath", -41.2930, 174.8000, "suburb", ()),
    # --- south ---
    ("Newtown", -41.3110, 174.7800, "suburb", ("riddiford street",)),
    ("Berhampore", -41.3210, 174.7740, "suburb", ()),
    ("Island Bay", -41.3400, 174.7720, "suburb", ("the parade island bay",)),
    ("Owhiro Bay", -41.3450, 174.7530, "suburb", ("owhiro bay parade",)),
    ("Houghton Bay", -41.3390, 174.7840, "suburb", ()),
    ("Melrose", -41.3260, 174.7860, "suburb", ()),
    ("Vogeltown", -41.3130, 174.7690, "suburb", ()),
    ("Brooklyn", -41.3050, 174.7620, "suburb", ("ohiro road",)),
    ("Kingston", -41.3160, 174.7550, "suburb", ()),
    ("Happy Valley", -41.3320, 174.7580, "suburb", ()),
    ("Mornington", -41.3200, 174.7660, "suburb", ()),
    ("Southgate", -41.3330, 174.7720, "suburb", ()),
    # --- east ---
    ("Hataitai", -41.3050, 174.7930, "suburb", ("waipapa road",)),
    ("Kilbirnie", -41.3170, 174.7930, "suburb", ("kilbirnie crescent",)),
    ("Lyall Bay", -41.3290, 174.7960, "suburb", ()),
    ("Rongotai", -41.3260, 174.8010, "suburb", ()),
    ("Miramar", -41.3170, 174.8160, "suburb", ("miramar avenue",)),
    ("Strathmore Park", -41.3330, 174.8180, "suburb", ("strathmore",)),
    ("Seatoun", -41.3260, 174.8320, "suburb", ()),
    ("Breaker Bay", -41.3350, 174.8290, "suburb", ()),
    ("Karaka Bays", -41.3130, 174.8300, "suburb", ()),
    ("Evans Bay", -41.3080, 174.8030, "suburb", ("evans bay parade",)),
    ("Shelly Bay", -41.3070, 174.8180, "suburb", ()),
    # --- west ---
    ("Karori", -41.2840, 174.7380, "suburb", ("karori road",)),
    ("Makara", -41.2340, 174.6500, "suburb", ()),
    ("Wilton", -41.2720, 174.7550, "suburb", ()),
    ("Wadestown", -41.2700, 174.7690, "suburb", ()),
    ("Crofton Downs", -41.2600, 174.7620, "suburb", ()),
    # --- north ---
    ("Ngaio", -41.2530, 174.7660, "suburb", ()),
    ("Khandallah", -41.2440, 174.7830, "suburb", ()),
    ("Broadmeadows", -41.2420, 174.7930, "suburb", ()),
    ("Kaiwharawhara", -41.2600, 174.7860, "suburb", ()),
    ("Johnsonville", -41.2280, 174.8040, "suburb", ("johnsonville road",)),
    ("Newlands", -41.2260, 174.8180, "suburb", ()),
    ("Paparangi", -41.2170, 174.8130, "suburb", ()),
    ("Woodridge", -41.2160, 174.8250, "suburb", ()),
    ("Churton Park", -41.2050, 174.8020, "suburb", ()),
    ("Grenada Village", -41.2000, 174.8180, "suburb", ("grenada north",)),
    ("Horokiwi", -41.2200, 174.8400, "suburb", ()),
    ("Tawa", -41.1720, 174.8250, "suburb", ()),
    ("Ohariu", -41.2050, 174.7300, "suburb", ()),
    # --- landmarks and choke points ---
    ("Ngauranga Gorge", -41.2440, 174.8090, "landmark", ("ngauranga",)),
    ("Terrace Tunnel", -41.2870, 174.7720, "landmark", ()),
    ("Mount Victoria Tunnel", -41.2960, 174.7880, "landmark", ("mt victoria tunnel",)),
    ("Karori Tunnel", -41.2880, 174.7530, "landmark", ()),
    ("Wellington Railway Station", -41.2790, 174.7804, "landmark", ()),
    ("Wellington Airport", -41.3272, 174.8053, "landmark", ()),
    ("Wellington Hospital", -41.3080, 174.7800, "landmark", ()),
    ("Basin Reserve", -41.3010, 174.7810, "landmark", ()),
    ("Cuba Street", -41.2930, 174.7740, "landmark", ()),
    ("Lambton Quay", -41.2830, 174.7760, "landmark", ()),
    ("Courtenay Place", -41.2930, 174.7820, "landmark", ()),
    ("Interislander Terminal", -41.2740, 174.7830, "landmark", ("ferry terminal",)),
    # --- wider region ---
    ("Petone", -41.2260, 174.8720, "suburb", ()),
    ("Lower Hutt", -41.2170, 174.9080, "town", ("hutt city",)),
    ("Upper Hutt", -41.1250, 175.0700, "town", ()),
    ("Wainuiomata", -41.2620, 174.9450, "town", ()),
    ("Eastbourne", -41.2920, 174.9010, "town", ()),
    ("Days Bay", -41.2830, 174.9070, "suburb", ()),
    ("Porirua", -41.1330, 174.8400, "town", ()),
    ("Kapiti Coast", -40.9160, 175.0000, "region", ("kapiti", "paraparaumu")),
    ("Cook Strait", -41.3000, 174.6000, "region", ()),
    # Deliberately last and deliberately vague: a fallback, not a location.
    ("Wellington", -41.2866, 174.7756, "region", ("te whanganui-a-tara", "wgtn", "welly")),
]

# How much to trust a match, by how specific the kind of place is.
KIND_CONFIDENCE = {
    "landmark": 0.75,
    "suburb": 0.60,
    "town": 0.45,
    "region": 0.20,
}
