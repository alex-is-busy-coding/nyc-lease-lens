# NYC Lease Lens

An AI agent that checks any NYC apartment for red flags before you sign the lease.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and the [gcloud CLI](https://cloud.google.com/sdk/docs/install) (`brew install --cask google-cloud-sdk`).

```bash
make setup   # create .env, install deps, log in to Google Cloud, enable Vertex AI
make dev     # start with auto-reload at http://127.0.0.1:8000
```

Run `make help` to list every target. 
Config lives in `.env` (copied from `.env.example`, gitignored)
Override any value per call, e.g. `make dev PORT=9000`.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.