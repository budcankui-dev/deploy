import argparse
import sys

from app.receiver import main as receiver_main
from app.sender import main as sender_main


def main():
    parser = argparse.ArgumentParser(description="Unified entry for sender/receiver node")
    parser.add_argument("--role", choices=["sender", "receiver"], required=True)
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]

    if args.role == "sender":
        sender_main()
    else:
        receiver_main()


if __name__ == "__main__":
    main()
