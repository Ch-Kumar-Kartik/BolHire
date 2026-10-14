from fastapi import FastAPI

app = FastAPI(title="BolHire API")


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
