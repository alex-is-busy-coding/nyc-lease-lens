import requests

SOCRATA_URL = "https://data.cityofnewyork.us/resource/{dataset}.json"
GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
TIMEOUT = 15


def geosearch(text: str, size: int = 10) -> list[dict]:
    """Search NYC addresses. Returns GeoJSON features, best match first."""
    r = requests.get(GEOSEARCH_URL, params={"text": text, "size": size}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["features"]


def socrata(dataset: str, params: dict) -> list[dict]:
    """Run a SoQL query against an NYC Open Data dataset."""
    r = requests.get(SOCRATA_URL.format(dataset=dataset), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
