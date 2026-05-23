# Render Deployment

Deploy the backend as a Docker Web Service.

## Settings

- Root directory: `backend`
- Environment: Docker
- Health check path: `/health`
- Port: `8000`

## Local Docker Check

```bash
cd backend
docker build -t md-to-word-backend .
docker run --rm -p 8000:8000 md-to-word-backend
```

Then check:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok","engine":"pandoc"}
```

## Notes

Render free web services may sleep after being idle. The extension should show a clear service-unavailable or retry message when the backend is waking up.
