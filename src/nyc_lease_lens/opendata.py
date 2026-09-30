import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class OpenDataClient:
    """NYC GeoSearch and NYC Open Data (Socrata). Neither needs an API key."""

    def __init__(self, geosearch_url: str, socrata_url: str, timeout: float, retries: int = 2):
        self.geosearch_url = geosearch_url
        self.socrata_url = socrata_url
        self.timeout = timeout
        self.http = requests.Session()
        # Socrata response times spike now and then; a retry usually lands on a fast one.
        retry = Retry(total=retries, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
        self.http.mount("https://", HTTPAdapter(max_retries=retry))

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        """Search NYC addresses. Returns GeoJSON features, best match first."""
        return self._get(self.geosearch_url, {"text": text, "size": size})["features"]

    def socrata(self, dataset: str, params: dict) -> list[dict]:
        """Run a SoQL query against an NYC Open Data dataset."""
        return self._get(self.socrata_url.format(dataset=dataset), params)

    def _get(self, url: str, params: dict):
        r = self.http.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
