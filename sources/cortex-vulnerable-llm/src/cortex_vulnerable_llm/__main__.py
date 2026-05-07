"""Allow ``python -m cortex_vulnerable_llm ...`` as a CLI entrypoint."""
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
