from pathlib import Path

from math_research_agent.tools import AgentToolExecutor, build_tool_payload


def test_workspace_tools_are_scoped_and_support_basic_coding_flow(tmp_path: Path):
    executor = AgentToolExecutor(tmp_path)

    assert (
        executor("write", {"path": "notes/demo.txt", "content": "hello\nworld"})["status"] == "OK"
    )
    edited = executor(
        "edit",
        {"path": "notes/demo.txt", "old_text": "world", "new_text": "agent"},
    )
    assert edited["replacements"] == 1
    assert executor("read", {"path": "notes/demo.txt"})["content"] == "hello\nagent"
    assert executor("grep", {"pattern": "agent", "path": "notes"})["matches"][0]["line"] == 2
    assert "notes/demo.txt" in executor("find", {"pattern": "**/*.txt"})["matches"]
    assert executor("bash", {"command": "pwd"})["stdout"].strip() == str(tmp_path)

    escaped = executor("read", {"path": "../outside.txt"})
    assert escaped["status"] == "ERROR"
    assert "escapes" in escaped["error"]


def test_common_tools_render_for_gemini_and_openrouter_responses():
    names = ["read", "bash", "edit", "write", "grep", "find", "web_search"]
    gemini = build_tool_payload(names, provider="gemini")
    assert gemini[0]["functionDeclarations"][0]["name"] == "read"
    openrouter = build_tool_payload(names, provider="openai_compatible")
    tool = openrouter[0]
    assert tool["type"] == "function"
    assert tool["name"] == "read"
    assert tool["description"] == "Read a UTF-8 text file from the workspace."
    assert tool["parameters"]["required"] == ["path"]
    assert "function" not in tool
