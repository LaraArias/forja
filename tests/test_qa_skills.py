"""Tests for QA agent skill configurations and Playwright helper."""

import ast
import json

from forja.init import get_template


class TestPlaywrightHelperSyntax:
    """Verify forja_qa_playwright.py is syntactically valid Python."""

    def test_parses_without_errors(self):
        source = get_template("forja_qa_playwright.py")
        # ast.parse raises SyntaxError if invalid
        tree = ast.parse(source)
        assert tree is not None

    def test_has_run_qa_function(self):
        source = get_template("forja_qa_playwright.py")
        assert "async def run_qa(" in source

    def test_has_main_guard(self):
        source = get_template("forja_qa_playwright.py")
        assert 'if __name__ == "__main__"' in source


class TestLandingPageQAAgent:
    """Verify landing-page skill QA agent uses Playwright."""

    def test_qa_agent_exists(self):
        content = get_template("skills/landing-page/agents.json")
        data = json.loads(content)
        agents = data["agents"]
        qa_agents = [a for a in agents if a["name"] == "qa"]
        assert len(qa_agents) == 1

    def test_qa_prompt_mentions_playwright(self):
        content = get_template("skills/landing-page/agents.json")
        data = json.loads(content)
        qa = next(a for a in data["agents"] if a["name"] == "qa")
        prompt = qa["prompt"].lower()
        assert "playwright" in prompt

    def test_qa_prompt_mentions_screenshots(self):
        content = get_template("skills/landing-page/agents.json")
        data = json.loads(content)
        qa = next(a for a in data["agents"] if a["name"] == "qa")
        assert "screenshot" in qa["prompt"].lower()


class TestApiBackendQAAgent:
    """Verify api-backend skill QA agent uses httpx."""

    def test_qa_agent_exists(self):
        content = get_template("skills/api-backend/agents.json")
        data = json.loads(content)
        agents = data["agents"]
        qa_agents = [a for a in agents if a["name"] == "qa"]
        assert len(qa_agents) == 1

    def test_qa_prompt_mentions_httpx(self):
        content = get_template("skills/api-backend/agents.json")
        data = json.loads(content)
        qa = next(a for a in data["agents"] if a["name"] == "qa")
        assert "httpx" in qa["prompt"].lower()

    def test_qa_prompt_mentions_error_cases(self):
        content = get_template("skills/api-backend/agents.json")
        data = json.loads(content)
        qa = next(a for a in data["agents"] if a["name"] == "qa")
        prompt = qa["prompt"]
        assert "404" in prompt
        assert "422" in prompt
