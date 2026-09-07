"""Regenerate checked-in Protocol v2 schemas and TypeScript declarations."""

from __future__ import annotations

from oceanx.protocol.v2.schema import write_protocol_artifacts


def main() -> None:
    request_path, event_path, type_path = write_protocol_artifacts()
    print(request_path)
    print(event_path)
    print(type_path)


if __name__ == "__main__":
    main()
