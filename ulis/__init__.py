"""Universal Legal Intelligence System (ULIS) backend.

The package is split into independent modules that mirror the architecture in
``docs/architecture.md``:

* ``ulis.jurisdictions`` - versioned jurisdiction profiles and jurisdiction-specific modules
* ``ulis.sources``       - source registry (coverage, licence), connectors and parsers
* ``ulis.storage``       - private, content-addressed object storage for raw source documents
* ``ulis.ingestion``     - the ingestion pipeline (fetch -> store -> parse -> normalise -> index)
* ``ulis.temporal``      - point-in-time validity resolution
* ``ulis.retrieval``     - jurisdiction-aware hybrid retrieval and authority ranking
* ``ulis.verification``  - integrity, quotation and legal-status checks
* ``ulis.api``           - FastAPI HTTP interface
"""

__version__ = "0.1.0"
