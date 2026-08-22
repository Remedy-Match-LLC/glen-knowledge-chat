"""practitioners table CRUD + search.

Two SQL-building functions are unit-tested as pure strings (build_search_sql,
upsert_sql_and_params). The actual execution helpers (run_upsert, run_search,
list_ungeocoded, update_geocode) are thin shims over psycopg2 — integration
tested in Task 13 against the real Supabase instance."""
import re
from typing import Optional, Tuple

from db_supabase import supabase_cursor
from scrapers.practitioner_finder.normalize import normalize_country


MILES_TO_METERS = 1609.344

_OT_CREDENTIAL_RE = re.compile(
    r"(^|[^A-Za-z])(OTR/L|OTR|OTD|MOT|MSOT|OT)([^A-Za-z]|$)", re.I,
)
_PT_CREDENTIAL_RE = re.compile(
    r"(^|[^A-Za-z])(DPT|MPT|MSPT|PT)([^A-Za-z]|$)", re.I,
)


def profession_specialties(credentials: Optional[str]) -> list[str]:
    """Return explicit profession tags found in a qualified source row.

    This does not qualify a practitioner by itself; adapters already represent
    vetted organizations or advanced credentials. It only makes profession a
    first-class, cross-specialty Finder facet for those curated rows.
    """
    value = credentials or ""
    lowered = value.lower()
    tags: list[str] = []
    if "occupational therapist" in lowered or _OT_CREDENTIAL_RE.search(value):
        tags.append("occupational_therapy")
    if ("physical therapist" in lowered or "physiotherapist" in lowered
            or _PT_CREDENTIAL_RE.search(value)):
        tags.append("physical_therapy")
    return tags


def build_search_sql(
    *,
    lat: float,
    lng: float,
    radius_miles: float,
    specialties: Optional[list[str]],
    tiers: Optional[list[str]],
    limit: int,
    fellowship_only: bool = False,
    countries: Optional[list[str]] = None,
    profession: Optional[str] = None,
) -> Tuple[str, list]:
    """Build the search SQL string and parameter tuple.

    psycopg2 substitutes %s in SQL-text order. SELECT appears before WHERE in
    the string, so its lat/lng come FIRST in params, then WHERE's lat/lng/radius,
    then optional filters. Param layout:
        [select_lat, select_lng,
         where_lat, where_lng, radius_meters,
         specialties? (if filtered),
         tiers? (if filtered)]

    fellowship_only=True narrows to rows where fellowship_level = true. Used
    by the UI's "Fellows Only" toggle to surface top-tier credentialed
    practitioners (FCOVD, MIAOMT, MIABDM, FOWNS, etc.).

    The WHERE pair `earth_box(...) @> ll_to_earth(lat, lng)` + the precise
    `earth_distance(...) < radius` is the canonical earthdistance pattern.
    earth_box yields a cube the GiST index on `ll_to_earth(lat, lng)` can
    accelerate via the @> operator (bounding-box pre-filter); the precise
    earth_distance clause then narrows to a true circle. Without the earth_box
    half, the planner does a Seq Scan even with the GiST index present."""
    radius_meters = radius_miles * MILES_TO_METERS
    where_clauses = [
        "earth_box(ll_to_earth(%s, %s), %s) @> ll_to_earth(lat, lng)",
        "earth_distance(ll_to_earth(%s, %s), ll_to_earth(lat, lng)) < %s",
    ]
    where_params: list = [
        lat, lng, radius_meters,   # earth_box bounding-box (GiST-accelerated)
        lat, lng, radius_meters,   # earth_distance precise circle
    ]

    if specialties:
        where_clauses.append("specialties && %s")
        where_params.append(specialties)

    if tiers:
        where_clauses.append("tier = ANY(%s)")
        where_params.append(tiers)

    if countries:
        # Defense-in-depth: a US-ZIP search constrains results to US rows so a
        # stray mis-geocoded coordinate (foreign row that slipped through) can
        # never surface in a domestic search. See geocode.mapbox_country_filter.
        where_clauses.append("country = ANY(%s)")
        where_params.append(countries)

    if profession == "occupational_therapist":
        # NORA exposes the provider's profession in its credentials field.
        # Match the full role name rather than the ambiguous abbreviation
        # "OT", which appears in unrelated words and credentials.
        where_clauses.append(
            "(specialties && %s OR LOWER(COALESCE(credentials, '')) LIKE %s "
            "OR COALESCE(credentials, '') ~* %s)"
        )
        where_params.extend([
            ["occupational_therapy"], "%occupational therapist%",
            "(^|[^A-Za-z])(OTR/L|OTR|OTD|MOT|MSOT|OT)([^A-Za-z]|$)",
        ])
    elif profession == "physical_therapist":
        # Source directories variously publish the full profession or a
        # standalone PT-family credential. The surrounding non-letter guards
        # prevent short "PT" from matching unrelated words.
        where_clauses.append(
            "(specialties && %s OR LOWER(COALESCE(credentials, '')) LIKE %s OR "
            "COALESCE(credentials, '') ~* %s)"
        )
        where_params.extend([
            ["physical_therapy"], "%physical therapist%",
            "(^|[^A-Za-z])(DPT|MPT|MSPT|PT)([^A-Za-z]|$)",
        ])

    if fellowship_only:
        where_clauses.append("fellowship_level = true")

    sql = f"""
        SELECT id, tier, source_org, fellowship_level, specialties,
               name, practice_name, credentials,
               phone, email, website,
               address1, city, state, postal, country,
               lat, lng, geocode_quality,
               photo_url, bio, accepting_new_patients, telehealth,
               accepts_inquiries, modules_completed, show_contact,
               products, order_options,
               earth_distance(ll_to_earth(lat, lng), ll_to_earth(%s, %s)) / {MILES_TO_METERS:.4f}
                 AS distance_miles
        FROM v_practitioners_public
        WHERE {' AND '.join(where_clauses)}
        ORDER BY distance_miles ASC
        LIMIT {int(limit)}
    """
    # SELECT params (consumed first by psycopg2) come before WHERE params.
    params = [lat, lng] + where_params
    return sql, params


def upsert_sql_and_params(row_dict: dict) -> Tuple[str, list]:
    """Build INSERT ... ON CONFLICT (source_url) DO UPDATE SQL + params.

    Idempotent: re-running with the same source_url updates the existing row."""
    cols = list(row_dict.keys())
    params = [row_dict[c] for c in cols]
    col_sql = ", ".join(cols)
    placeholder_sql = ", ".join(["%s"] * len(cols))
    update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "source_url")

    # The unique index on source_url is partial (WHERE source_url IS NOT NULL),
    # so ON CONFLICT must repeat that predicate for Postgres to match it.
    # Otherwise: "no unique or exclusion constraint matching the ON CONFLICT specification".
    sql = f"""
        INSERT INTO practitioners ({col_sql}, last_scraped_at)
        VALUES ({placeholder_sql}, now())
        ON CONFLICT (source_url) WHERE source_url IS NOT NULL
        DO UPDATE SET {update_sql}, last_scraped_at = now(), updated_at = now()
    """
    return sql, params


def _normalize_for_write(row_dict: dict) -> dict:
    # Normalize the country to an ISO-2 code at the single write boundary so
    # every scraped row stores a clean value (adapters stay unchanged).
    if "country" in row_dict:
        row_dict["country"] = normalize_country(row_dict.get("country"))
    existing = list(row_dict.get("specialties") or [])
    for tag in profession_specialties(row_dict.get("credentials")):
        if tag not in existing:
            existing.append(tag)
    row_dict["specialties"] = existing
    return row_dict


def run_upsert(row_dict: dict) -> None:
    row_dict = _normalize_for_write(row_dict)
    sql, params = upsert_sql_and_params(row_dict)
    with supabase_cursor() as cur:
        cur.execute(sql, params)


def run_upsert_many(rows: list[dict]) -> int:
    """Upsert many rows over ONE connection. Returns the number written.

    run_upsert() opens a fresh connection per row, which is fine for the small
    practitioner adapters but pathological for bulk farm sources (USDA alone is
    ~14k rows = ~14k SSL handshakes, i.e. hours). Same SQL, same normalization —
    only the connection is shared, and the whole batch commits atomically on
    exit."""
    if not rows:
        return 0
    written = 0
    with supabase_cursor() as cur:
        for row_dict in rows:
            sql, params = upsert_sql_and_params(_normalize_for_write(row_dict))
            cur.execute(sql, params)
            written += 1
    return written


def run_search(
    *,
    lat: float,
    lng: float,
    radius_miles: float,
    specialties: Optional[list[str]],
    tiers: Optional[list[str]],
    limit: int = 200,
    fellowship_only: bool = False,
    countries: Optional[list[str]] = None,
    profession: Optional[str] = None,
) -> list[dict]:
    sql, params = build_search_sql(
        lat=lat, lng=lng, radius_miles=radius_miles,
        specialties=specialties, tiers=tiers, limit=limit,
        fellowship_only=fellowship_only, countries=countries,
        profession=profession,
    )
    with supabase_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def list_ungeocoded() -> list[dict]:
    """Rows that have geocodable input but no lat/lng yet."""
    sql = """
        SELECT id, address1, city, state, postal, country
        FROM practitioners
        WHERE lat IS NULL
          AND removal_requested = false
          AND (
            (address1 IS NOT NULL AND city IS NOT NULL AND state IS NOT NULL)
            OR (city IS NOT NULL AND state IS NOT NULL)
            OR postal IS NOT NULL
            OR state IS NOT NULL
          )
    """
    with supabase_cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def update_geocode(
    practitioner_id: str,
    lat: Optional[float],
    lng: Optional[float],
    quality: Optional[str],
) -> None:
    sql = """
        UPDATE practitioners
        SET lat = %s, lng = %s, geocode_quality = %s, updated_at = now()
        WHERE id = %s
    """
    with supabase_cursor() as cur:
        cur.execute(sql, (lat, lng, quality, practitioner_id))
