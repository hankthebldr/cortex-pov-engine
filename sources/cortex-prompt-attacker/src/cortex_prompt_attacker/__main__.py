"""Allow ``python -m cortex_prompt_attacker ...`` as a CLI entrypoint."""
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
