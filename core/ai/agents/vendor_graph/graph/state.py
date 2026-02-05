from __future__ import annotations

import operator
from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage



class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
