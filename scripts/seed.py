"""Seed demo data and index the sample knowledge base:  python scripts/seed.py [--reset]

--reset drops the SQL tables and empties the vector collection first.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config.settings import get_settings  # noqa: E402
from app.models.db import Base  # noqa: E402
from app.services.bootstrap import bootstrap  # noqa: E402
from app.services.container import build_container  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe existing data first")
    args = ap.parse_args()

    settings = get_settings()
    Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True) if settings.database_url.startswith("sqlite") else None
    container = build_container(settings)
    if args.reset:
        engine = container.session_factory.kw["bind"]
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        container.store.reset()
        container.cache.delete("kb:version")
    print(f"LLM provider: {container.llm.name}   embeddings: {container.embedder.model}")
    print(bootstrap(container))


if __name__ == "__main__":
    main()
