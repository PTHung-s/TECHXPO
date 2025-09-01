# Deploy TECHXPO Kiosk to Google Cloud Run

## 1. Prerequisites
- Google Cloud project (gcloud CLI authenticated: `gcloud auth login` + `gcloud config set project <PROJECT_ID>`)
- Enable APIs:
  - Cloud Run API
  - Artifact Registry API
- LiveKit Cloud / self-host credentials available via environment variables at deploy time
- Gemini (Google AI) API key (`GEMINI_API_KEY`) if not already handled by livekit plugin (place in Secret Manager recommended)

## 2. Repository Layout
You placed `Dockerfile` inside `TECHXPO/` which is fine. Build context must be that folder when building.

```
sesame/
  TECHXPO/
    Dockerfile
    entrypoint.sh
    requirements.txt
    web/
    *.py
```

## 3. Build & Push (Artifact Registry)
Create (once):
```
gcloud artifacts repositories create techxpo-repo \
  --repository-format=docker --location=asia-southeast1 --description="TechXPO images"
```
Tag vars:
```
PROJECT_ID=$(gcloud config get-value project)
REGION=asia-southeast1
REPO=techxpo-repo
IMAGE=techxpo-kiosk
FULL=asia-southeast1-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE:latest
```
Build (context is TECHXPO folder):
```
cd TECHXPO
gcloud builds submit --tag $FULL .
```
(Or use `docker build -t $FULL . && docker push $FULL` if you prefer local docker + `gcloud auth configure-docker`.)

## 4. Deploy to Cloud Run
Basic deploy (public unauthenticated):
```
gcloud run deploy techxpo-kiosk \
  --image $FULL \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 1Gi \
  --min-instances=0 --max-instances=5
```

## 5. Environment Variables
Add what you use locally:
- LIVEKIT_URL
- LIVEKIT_API_KEY
- LIVEKIT_API_SECRET
- GEMINI_API_KEY (if required by plugin)
- AGENT_NAME (defaults `kiosk`)
- RUN_AGENT=1 (set 0 if you only want token/static server)

Example update:
```
gcloud run services update techxpo-kiosk \
  --region $REGION \
  --set-env-vars LIVEKIT_URL=...,LIVEKIT_API_KEY=...,LIVEKIT_API_SECRET=...,RUN_AGENT=1
```

Better: store sensitive values in Secret Manager and mount:
```
gcloud secrets create livekit-secret --data-file=- <<<"LIVEKIT_API_KEY=...\nLIVEKIT_API_SECRET=..."
```
(Then use `--set-secrets` mapping or individual secrets.)

## 6. Concurrency & Timeouts
Realtime agent holds a websocket; keep instance concurrency low:
```
--concurrency=5 --timeout=900
```
Tune as needed.

## 7. Health Check
Cloud Run hits `/` -> static index, and our Docker HEALTHCHECK uses `/healthz`. If you see start failures increase `--timeout` or remove HEALTHCHECK.

## 8. Separating Worker vs Web (Optional)
If heavy CPU:
- Service A: token/static server (`RUN_AGENT=0`)
- Service B: agent worker only (`CMD ["python","gemini_kiosk.py"]` or `RUN_AGENT=1` and no uvicorn by overriding command)

Deploy worker variant:
```
gcloud run deploy techxpo-worker \
  --image $FULL --region $REGION \
  --command python --args gemini_kiosk.py \
  --set-env-vars RUN_AGENT=1,PORT=8080
```
Then point web service's `RoomAgentDispatch(agent_name=kiosk)` to worker domain via LIVEKIT infra (if architecture supports multi-service).

## 9. Local Test
```
cd TECHXPO
docker build -t local/techxpo .
docker run -it --rm -p 8080:8080 \
  -e LIVEKIT_URL=... -e LIVEKIT_API_KEY=... -e LIVEKIT_API_SECRET=... \
  -e RUN_AGENT=0 local/techxpo
```
Open http://localhost:8080

## 10. Logs & Debugging
```
gcloud run services describe techxpo-kiosk --region $REGION
gcloud logs tail --region $REGION --service techxpo-kiosk
```

## 11. Zero-Downtime Update
Rebuild + new tag, redeploy with `--tag` or explicit digest. Use traffic splitting for canary if needed.

## 12. Scaling Considerations
- If voice / realtime connections are long-lived, keep `min-instances=1` to reduce cold start.
- For higher throughput, consider splitting worker & web.

## 13. Next Steps
- Add auth (API keys or signed URLs) if public token issuance is risky.
- Migrate secrets to Secret Manager.
- Add CI workflow (GitHub Actions) to build & deploy automatically on push to main.

---
Questions: open an issue or ask in chat.
