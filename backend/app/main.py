from fastapi import FastAPI

app = FastAPI(title="SpendWise AI API")


@app.get("/")
def read_root():
    return {"message": "SpendWise AI API is running"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
