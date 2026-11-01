import json

from app.modules.datasets.application.nasa_ims_model import train_nasa_ims_model
from app.shared.database.session import SessionLocal


def main() -> None:
    with SessionLocal() as db:
        result = train_nasa_ims_model(db)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
