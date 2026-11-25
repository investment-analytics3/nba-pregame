import sys

from . import CONFIG, update

if __name__ == "__main__":
    sports = [a for a in sys.argv[1:] if not a.startswith("-")] or list(CONFIG)
    for s in sports:
        print(update(s, fetch="--no-fetch" not in sys.argv))
