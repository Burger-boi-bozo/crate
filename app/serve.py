"""Single-process Crate entry point."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.runtime:app",
        host=os.getenv("CRATE_BIND_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        workers=1,
    )
