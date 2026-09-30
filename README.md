<p align="center">
  <img src="assets/nyc_lease_lens_logo.jpeg" alt="NYC Lease Lens logo" width="480">
</p>

# NYC Lease Lens

An AI agent that checks any NYC apartment for red flags before you sign the lease.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and the [gcloud CLI](https://cloud.google.com/sdk/docs/install) (`brew install --cask google-cloud-sdk`).

```bash
make setup   # create .env, install deps, log in to Google Cloud, enable Vertex AI
make dev     # start with auto-reload at http://127.0.0.1:8000
```

Run `make help` to list every target.
Config lives in `.env` (copied from `.env.example`, gitignored).
Override any value per call, e.g. `make dev PORT=9000`.

## Tools

The agent calls these tools to check a building. The table is generated from `src/nyc_lease_lens/tools/`; run `make docs` to refresh it.

<!-- tools:start -->
| Tool | What it does | Parameters |
| --- | --- | --- |
| `lookup_building` | Identify an NYC building from a street address. | `address` (string, required): Street address, e.g. '157 Ludlow St' or '89-11 Queens Blvd'<br>`borough` (string, optional): Borough, if the user mentioned it or it is clear from context. One of: `Manhattan`, `Bronx`, `Brooklyn`, `Queens`, `Staten Island`. |
| `get_hpd_violations` | Summarize HPD housing code violations for a building, by severity class and type (pests, mold, heat/hot water, lead paint, fire safety, illegal occupancy, ...). | `bbl` (string, required): 10-digit BBL from lookup_building<br>`months` (integer, optional, 1–120): How far back to count violations, open or closed. Default 36. |
| `get_311_complaints` | Summarize 311 complaints about a building (heat/hot water, pests, mold, leaks, noise, sanitation, repairs) and compare it with nearby buildings per apartment, as a percentile: 90 means more complaints per unit than 90% of nearby buildings. | `bbl` (string, required): 10-digit BBL from lookup_building<br>`latitude` (number, optional): Building latitude from lookup_building<br>`longitude` (number, optional): Building longitude from lookup_building<br>`categories` (string[], optional): Only report these categories. Omit for all. One of: `heat_hot_water`, `pests`, `mold`, `leaks_plumbing`, `noise`, `sanitation`, `repairs`.<br>`radius_m` (integer, optional, 50–500): Radius in meters for the nearby-building comparison. Default 150.<br>`months` (integer, optional, 1–60): How far back to count complaints. Default 24. |
<!-- tools:end -->

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.