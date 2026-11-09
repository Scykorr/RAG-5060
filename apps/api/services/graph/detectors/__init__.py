from apps.api.services.graph.detectors.base import RelationCandidate, EvidenceCandidate
from apps.api.services.graph.detectors.lexical import LexicalSimilarityDetector
from apps.api.services.graph.detectors.references import ExplicitReferenceDetector
from apps.api.services.graph.detectors.versions import VersionRelationDetector

__all__ = [
    "RelationCandidate",
    "EvidenceCandidate",
    "LexicalSimilarityDetector",
    "ExplicitReferenceDetector",
    "VersionRelationDetector",
]
