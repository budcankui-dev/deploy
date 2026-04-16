import argparse
import sys

from apps.model_train.trainer_app import main as trainer_main
from apps.video_infer.receiver_app import main as receiver_main
from apps.video_infer.sender_app import main as sender_main


def main():
    parser = argparse.ArgumentParser(description="Unified entry for sender/receiver node")
    parser.add_argument("--role", choices=["sender", "receiver", "trainer"], required=True)
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]

    if args.role == "sender":
        sender_main()
    elif args.role == "receiver":
        receiver_main()
    else:
        trainer_main()


if __name__ == "__main__":
    main()
