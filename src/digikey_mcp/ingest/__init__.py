"""BOM ingest: read EDA / spreadsheet / text BOMs into normalised BomLine records."""

from .classify import extract_package, infer_category
from .core import FORMATS, ingest
from .models import (
    NON_PART_CATEGORIES,
    BomLine,
    IngestError,
    IngestResult,
    MappingReport,
    MissingDependencyError,
    UnparsedRow,
)
from .refdes import expand_refdes
from .values import ValueInfo, normalise_value

__all__ = [
    "FORMATS",
    "NON_PART_CATEGORIES",
    "BomLine",
    "IngestError",
    "IngestResult",
    "MappingReport",
    "MissingDependencyError",
    "UnparsedRow",
    "ValueInfo",
    "expand_refdes",
    "extract_package",
    "infer_category",
    "ingest",
    "normalise_value",
]
