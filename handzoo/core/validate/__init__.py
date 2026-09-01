"""The gates. Each returns a structured verdict, never a bare bool."""

from . import (ascii_gate, colour_gate, compile_gate, coverage_gate, delimiter_gate,
               pasted_gate, reference_gate, repetition_gate)
from .base import Failure, GateResult

__all__ = ["Failure", "GateResult", "ascii_gate", "colour_gate", "compile_gate",
           "coverage_gate", "delimiter_gate", "pasted_gate", "reference_gate",
           "repetition_gate"]
