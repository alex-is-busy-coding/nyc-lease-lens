import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nyc_lease_lens.config import OpenDataSettings
from nyc_lease_lens.log import ms_since

logger = logging.getLogger(__name__)


class OpenDataClient:
    """NYC GeoSearch and NYC Open Data (Socrata). Neither needs an API key."""

    def __init__(
        self,
        geosearch_url: str,
        socrata_url: str,
        timeout: float,
        retries: int = 2,
        slow_ms: int = 5000,
    ):
        self.geosearch_url = geosearch_url
        self.socrata_url = socrata_url
        self.timeout = timeout
        self.slow_ms = slow_ms
        self.http = requests.Session()
        # Socrata response times spike now and then; a retry usually lands on a fast one.
        retry = Retry(total=retries, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
        self.http.mount("https://", HTTPAdapter(max_retries=retry))

    @classmethod
    def from_settings(cls, settings: OpenDataSettings) -> "OpenDataClient":
        return cls(
            geosearch_url=settings.geosearch_url,
            socrata_url=settings.socrata_url,
            timeout=settings.timeout,
            retries=settings.retries,
            slow_ms=settings.slow_ms,
        )

    def geosearch(self, text: str, size: int = 10) -> list[dict]:
        """Search NYC addresses. Returns GeoJSON features, best match first."""
        return self._get("geosearch", self.geosearch_url, {"text": text, "size": size})["features"]

    def socrata(self, dataset: str, params: dict) -> list[dict]:
        """Run a SoQL query against an NYC Open Data dataset."""
        return self._get(dataset, self.socrata_url.format(dataset=dataset), params)

    def _get(self, source: str, url: str, params: dict):
        started = time.perf_counter()
        try:
            r = self.http.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            logger.warning(
                "open data request failed",
                extra={"source": source, "duration_ms": ms_since(started), "error": str(e)[:200]},
            )
            raise

        duration_ms = ms_since(started)
        fields: dict[str, object] = {"source": source, "duration_ms": duration_ms, "status": r.status_code}
        if isinstance(data, list):
            fields["rows"] = len(data)
        if duration_ms >= self.slow_ms:
            logger.warning("slow open data request", extra=fields)
        else:
            logger.debug("open data request", extra=fields | {"params": _short(params)})
        return data


def _short(params: dict, limit: int = 200) -> str:
    text = " ".join(f"{k}={v}" for k, v in params.items())
    return text if len(text) <= limit else text[:limit] + "..."
