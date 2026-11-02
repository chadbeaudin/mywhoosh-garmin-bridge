"""One-time interactive Garmin login: `docker exec -it mywhoosh-garmin-bridge python -m bridge.garmin_login`."""
import os
from pathlib import Path

from .garmin import GarminClient


def main() -> None:
    token_dir = Path(os.environ.get("DATA_DIR", "/data")) / "garmin_tokens"
    client = GarminClient(
        os.environ["GARMIN_USERNAME"],
        os.environ["GARMIN_PASSWORD"],
        token_dir,
        prompt_mfa=lambda: input("Garmin MFA code: ").strip(),
    )
    client.login()
    print(f"Garmin login OK; tokens saved in {token_dir}")


if __name__ == "__main__":
    main()
