"""Module entry: ``python -m lark_doc_whisper``."""
from .gateway.ws_gateway import main

if __name__ == "__main__":
    raise SystemExit(main())
