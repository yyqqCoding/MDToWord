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


class GateIntent(str, Enum):
    BUG_REPORT = "bug_report"
    FEATURE_REQUEST = "feature_request"
    UNRELATED = "unrelated"
    SPAM = "spam"
    UNKNOWN = "unknown"


class GateCategory(str, Enum):
    CONVERSION_CRASH = "conversion_crash"
    FORMULA_PARSING = "formula_parsing"
    TABLE_PARSING = "table_parsing"
    HEADING_PARSING = "heading_parsing"
    LIST_PARSING = "list_parsing"
    DOCX_STRUCTURE = "docx_structure"
    BACKEND_NORMALIZATION = "backend_normalization"
    EXTENSION_UI = "extension_ui"
    VISUAL_QUALITY = "visual_quality"
    UNKNOWN = "unknown"


class GateRoute(str, Enum):
    ACCEPTED_BACKEND_BUG = "accepted_backend_bug"
    REJECTED_IRRELEVANT = "rejected_irrelevant"
    QUARANTINED_SECURITY = "quarantined_security"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_HUMAN = "needs_human"
    DUPLICATE = "duplicate"
