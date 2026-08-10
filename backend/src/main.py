from fastapi import FastAPI

app = FastAPI(title="Galgame AI V2")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "runtime": "v2-foundation"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
