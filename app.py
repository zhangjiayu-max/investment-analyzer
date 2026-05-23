"""启动入口 — 委托到 backend/app.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

if __name__ == "__main__":
    from backend.app import app
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
else:
    from backend.app import app
