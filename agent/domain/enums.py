from enum import Enum


class FeedbackType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"


class FeedbackStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    GATING = "gating"
    ISSUE_REQUIRED = "issue_required"
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
    PUBLISHING_ISSUE = "publishing_issue"
    STALE_BASE = "stale_base"
    PR_OPENED = "pr_opened"
    ISSUE_OPENED = "issue_opened"


class AgentRunStatus(str, Enum):
    CREATED = "created"
    GATING = "gating"
    PREPARING_SOURCE = "preparing_source"
    REPRODUCING = "reproducing"
    REPAIRING = "repairing"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    PUBLISHING_ISSUE = "publishing_issue"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SECURITY_REJECTED = "security_rejected"
    STALE_BASE = "stale_base"


# 终态：运行不会再被 Scheduler 恢复。与 SupabaseAgentRunRepository.find_resumable
# 的状态过滤互为补集，新增状态时两处必须同步。
TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.BUDGET_EXHAUSTED,
        AgentRunStatus.SECURITY_REJECTED,
        AgentRunStatus.STALE_BASE,
    }
)


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


class GateArea(str, Enum):
    BACKEND = "backend"
    EXTENSION = "extension"
    CROSS_COMPONENT = "cross_component"
    NONE = "none"
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
    FEATURE_REQUEST = "feature_request"
    IRRELEVANT_CONTENT = "irrelevant_content"
    PROMPT_INJECTION = "prompt_injection"
    UNKNOWN = "unknown"


class GateRoute(str, Enum):
    ACCEPTED_BACKEND_BUG = "accepted_backend_bug"
    REJECTED_IRRELEVANT = "rejected_irrelevant"
    QUARANTINED_SECURITY = "quarantined_security"
    ISSUE_REQUIRED = "issue_required"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_HUMAN = "needs_human"
    DUPLICATE = "duplicate"
