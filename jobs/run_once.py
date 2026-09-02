from dotenv import load_dotenv

from src.prefs import load_prefs


def main() -> None:
    load_dotenv()
    load_prefs()
    print("dry-run: 0 listings")


if __name__ == "__main__":
    main()
