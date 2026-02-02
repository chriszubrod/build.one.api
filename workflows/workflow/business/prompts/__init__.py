# Agent prompts

from workflows.workflow.business.prompts.classification import (
    SYSTEM_PROMPT as CLASSIFICATION_SYSTEM_PROMPT,
    build_classification_prompt,
    build_multi_bill_prompt,
)
from workflows.workflow.business.prompts.approval_parse import (
    SYSTEM_PROMPT as APPROVAL_SYSTEM_PROMPT,
    build_approval_parse_prompt,
)
