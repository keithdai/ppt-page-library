"""PPTX export adapters."""

from pptlib.export.ooxml import (
    ExportError,
    ExportResult,
    SlideRef,
    export_slides,
)

__all__ = ["ExportError", "ExportResult", "SlideRef", "export_slides"]
