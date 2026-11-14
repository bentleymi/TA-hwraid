#!/usr/bin/env python3
"""Emit one JSON event per cycle with hardware RAID controller state."""
import sys

from hwraid_common import collect, emit


def main():
    emit(collect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
