"""Single-process web entry point. Multiple workers would split the queue."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.converter:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), workers=1)
