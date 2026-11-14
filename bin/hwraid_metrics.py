#!/usr/bin/env python3
"""Emit flat numeric metrics (one JSON event per cycle) for analytics."""
import sys

from hwraid_common import collect, emit, metrics_payload


def main():
    emit(metrics_payload(collect()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
