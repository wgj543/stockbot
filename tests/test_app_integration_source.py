"""Static safeguards for the integration where LINE SDK is not required in this test."""
import ast
from pathlib import Path
import unittest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class AppIntegrationSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_home_and_privacy_routes_are_preserved(self):
        route_paths = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        if decorator.func.attr == "route" and decorator.args:
                            arg = decorator.args[0]
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                route_paths.add(arg.value)
        self.assertIn("/", route_paths)
        self.assertIn("/privacy", route_paths)
        self.assertIn("/callback", route_paths)
        self.assertTrue(any(path.startswith("/chart/") for path in route_paths))

    def test_existing_command_handler_is_still_called_before_stock_lookup(self):
        handler = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "handle_message"
        )
        calls = [
            node.func.id for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertIn("handle_command", calls)
        self.assertIn("build_stock_reply_with_chart", calls)

    def test_text_only_helper_is_kept(self):
        self.assertIn("def build_stock_reply(user_message: str) -> str:", self.source)

    def test_chart_lookup_errors_are_isolated_from_legacy_quote(self):
        self.assertIn('logger.exception("即時走勢行情取得失敗，改用原有報價來源：%s", code)', self.source)
        self.assertIn('data = get_stock_data(code, stock_type)', self.source)
        self.assertIn('logger.exception("走勢圖產生失敗：%s", code)', self.source)

    def test_failed_combined_reply_attempts_text_fallback(self):
        self.assertIn('if chart_id:', self.source)
        self.assertIn('messages=[TextMessage(text=text[:LINE_TEXT_LIMIT])]', self.source)
        self.assertIn('logger.exception("LINE 文字備援回覆失敗")', self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
