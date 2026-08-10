from enum import Enum


class FeedbackType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"


class FeedbackStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    GATING = "gating"
    REJECTED_IRRELEVANT = "rejected_irrelevant"
    QUARANTINED_SECURITY = "quarantined_security"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_HUMAN = "needs_human"
    DUPLICATE = "duplicate"
    REPRODUCING = "reproducing"
    REPAIRING = "repairing"
    VALIDATING = "validating"
    CANNOT_REPRODUCE = "cannot_reproduce"
    SECURITY_REJECTED = "security_rejected"
    FAILED = "failed"
    VALIDATED = "validated"
    PUBLISHING = "publishing"
    STALE_BASE = "stale_base"
    PR_OPENED = "pr_opened"


class AgentRunStatus(str, Enum):
    CREATED = "created"
    GATING = "gating"
    PREPARING_SOURCE = "preparing_source"
    REPRODUCING = "reproducing"
    REPAIRING = "repairing"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SECURITY_REJECTED = "security_rejected"
    STALE_BASE = "stale_base"


class RiskLevel(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
