"""Build the model graph and consume streams with bounded completion retry."""

from __future__ import annotations

import re

from deepagents import (
    FilesystemPermission,
)
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from novel_agent.agent.freeform_policy import (
    FreeformOperationPolicy,
)
from novel_agent.agent.freeform_types import FreeformRunScope
from novel_agent.domain.errors import (
    InvalidChange,
)
from novel_agent.prompts.prompt import (
    ANALYST_PROMPT,
    EXECUTION_CORRECTION,
    main_agent_prompt,
)


class FreeformModelRunner:
    def __init__(self, build_model, build_agent):
        self.build_model = build_model
        self.build_agent = build_agent

    def prepare(self, scope: FreeformRunScope, tools):
        reads = tools[:5]
        model = self.build_model(scope.agent.models, scope.model_alias)
        # Deep Agents' filesystem backend is virtual Session state, never the Project root.
        # Only the named Commit tool can reach formal Project mutation services.
        agent = self.build_agent(
            model=model,
            tools=tools,
            subagents=[
                {
                    "name": "project_analyst",
                    "description": "只读分析 Project 文档中的情节与设定连续性",
                    "system_prompt": ANALYST_PROMPT,
                    "tools": reads[:-1],
                    "model": model,
                }
            ],
            system_prompt=main_agent_prompt(scope.selected_skill),
            skills=["/skills/"] if scope.available_skills else None,
            permissions=[
                FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
            ]
            if scope.available_skills
            else None,
        )

        return agent

    @staticmethod
    async def consume(
        scope: FreeformRunScope, agent, input_messages: list, expected, custom_names: set[str]
    ) -> str:
        final_message = ""
        async for mode, chunk in agent.astream(
            {
                "messages": input_messages,
                **({"files": scope.skill_files} if scope.skill_files else {}),
            },
            stream_mode=["messages", "updates"],
            config={"recursion_limit": 50},
        ):
            if mode == "messages":
                message, metadata = chunk
                if (
                    isinstance(message, AIMessageChunk)
                    and metadata.get("langgraph_node") == "model"
                ):
                    content = message.content
                    if isinstance(content, str) and content:
                        scope.result.content_chunks += 1
                        if (
                            scope.on_content
                            and FreeformOperationPolicy.completed(scope, expected)
                            and expected != "delete"
                            and not scope.result.draft_paths
                        ):
                            scope.on_content(content)
            elif mode == "updates":
                for update in chunk.values():
                    for message in update.get("messages", []) if isinstance(update, dict) else []:
                        if isinstance(message, AIMessage):
                            for call in message.tool_calls:
                                name = call["name"]
                                if name == "read_file":
                                    args = call.get("args", {})
                                    path = str(args.get("file_path") or args.get("path") or "")
                                    match = re.match(r"/?skills/([^/]+)/", path)
                                    if match:
                                        scope.used_skills.add(match.group(1))
                                        scope.emit("Reading", f"Skill {match.group(1)}")
                                if name not in custom_names and name not in scope.result.tool_calls:
                                    scope.result.tool_calls.append(name)
                                    scope.emit(
                                        "Delegating" if name == "task" else "Calling Tool",
                                        name,
                                    )
                            if isinstance(message.content, str) and message.content.strip():
                                final_message = message.content
        return final_message

    async def execute(self, scope: FreeformRunScope, agent, messages: list, tools) -> str:
        expected = FreeformOperationPolicy.expected(scope.task.prompt, scope.delete_target)
        names = {item.name for item in tools}
        final_message = await self.consume(scope, agent, messages, expected, names)
        if not FreeformOperationPolicy.completed(scope, expected):
            scope.emit("Retrying", f"missing {expected} tool result")
            correction = HumanMessage(content=EXECUTION_CORRECTION.format(operation=expected))
            final_message = await self.consume(
                scope,
                agent,
                [*messages, AIMessage(content=final_message or ""), correction],
                expected,
                names,
            )
        if not FreeformOperationPolicy.completed(scope, expected):
            raise InvalidChange(f"Agent did not complete requested {expected} operation")
        return final_message
