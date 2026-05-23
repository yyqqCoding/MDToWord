from fastapi import FastAPI

app = FastAPI(title="MD To Word Converter")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine": "pandoc",
    }
