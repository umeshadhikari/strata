"""Allow ``python -m strata`` to invoke the ingest entry point."""

from .ingest import main

if __name__ == "__main__":
    main()
