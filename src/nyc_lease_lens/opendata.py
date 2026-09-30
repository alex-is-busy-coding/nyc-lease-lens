import requests


class OpenDataClient:
    """NYC GeoSearch and NYC Open Data (Socrata). Neither needs an API key."""

    SOCRATA_URL = "https://data.cityofnewyork.us/resource/{dataset}.json"
    GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"

    def __init__(self, timeout: float = 15):
        self.timeout = timeout
        self.http = requests.Session()

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        """Search NYC addresses. Returns GeoJSON features, best match first."""
        return self._get(self.GEOSEARCH_URL, {"text": text, "size": size})["features"]

    def socrata(self, dataset: str, params: dict) -> list[dict]:
        """Run a SoQL query against an NYC Open Data dataset."""
        return self._get(self.SOCRATA_URL.format(dataset=dataset), params)

    def _get(self, url: str, params: dict):
        r = self.http.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
