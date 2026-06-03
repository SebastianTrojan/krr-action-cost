from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path

import krr_compiler
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType


ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "krr_compiler.py"


def run_compiler(spec_name: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(COMPILER), str(ROOT / "examples" / spec_name)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return completed.stdout


def run_compiler_with_stdin(spec_text: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(COMPILER)],
        input=spec_text,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return completed.stdout


def run_compiler_interactive(session_text: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(COMPILER), "--interactive"],
        input=session_text,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return completed.stdout


def run_compiler_failure(args: list[str], input_text: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COMPILER), *args],
        input=input_text,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


class TheoryExamplesTest(unittest.TestCase):
    def test_example_1(self) -> None:
        output = run_compiler("example1.krr")
        self.assertIn("RESULT 1: FALSE", output)
        self.assertIn("RESULT 2: FALSE", output)
        self.assertIn("RESULT 3: TRUE", output)
        self.assertIn("RESULT 4: FALSE", output)

    def test_example_2(self) -> None:
        output = run_compiler("example2.krr")
        self.assertIn("RESULT 1: TRUE", output)
        self.assertIn("RESULT 2: FALSE", output)

    def test_example_3(self) -> None:
        output = run_compiler("example3.krr")
        self.assertIn("RESULT 1: TRUE", output)
        self.assertIn("RESULT 2: TRUE", output)
        self.assertIn("RESULT 3: TRUE", output)

    def test_example_4(self) -> None:
        output = run_compiler("example4.krr")
        self.assertIn("The domain is inconsistent.", output)
        self.assertNotIn("QUERY 1:", output)
        self.assertNotIn("RESULT 1:", output)

    def test_example_5(self) -> None:
        output = run_compiler("example5.krr")
        self.assertIn("RESULT 1: TRUE", output)
        self.assertIn("RESULT 2: TRUE", output)
        self.assertIn("RESULT 3: TRUE", output)
        self.assertIn("RESULT 4: TRUE", output)
        self.assertIn("RESULT 5: TRUE", output)
        self.assertIn("RESULT 6: FALSE", output)
        self.assertIn("RESULT 7: TRUE", output)
        self.assertIn("RESULT 8: FALSE", output)
        self.assertIn("RESULT 9: TRUE", output)

    def test_stdin_spec_input(self) -> None:
        spec_text = """[domain]
initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5

        [queries]
doorOpen after openDoor
openDoor executable with cost 5
"""
        output = run_compiler_with_stdin(spec_text)
        self.assertIn("RESULT 1: FALSE", output)
        self.assertIn("RESULT 2: TRUE", output)

    def test_multiline_interactive_input(self) -> None:
        session_text = """[domain]
initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5
[queries]
doorOpen after openDoor
openDoor executable with cost 5
"""
        output = run_compiler_interactive(session_text)
        self.assertIn("RESULT 1: FALSE", output)
        self.assertIn("RESULT 2: TRUE", output)

    def test_comma_separated_domain_value_literals(self) -> None:
        spec_text = """[domain]
initially !doorOpen, hasKey
doorOpen, hasKey after openDoor
openDoor causes doorOpen if hasKey
openDoor costs 5

[queries]
doorOpen, hasKey after openDoor
openDoor executable with exact cost 5
"""
        output = run_compiler_with_stdin(spec_text)
        self.assertIn("RESULT 1: TRUE", output)
        self.assertIn("RESULT 2: TRUE", output)

    def test_comma_separated_effect_literals(self) -> None:
        spec_text = """[domain]
initially hasKey, alarmOn
openDoor causes doorOpen, !alarmOn if hasKey
openDoor costs 5

[queries]
doorOpen, !alarmOn after openDoor
openDoor executable with exact cost 5
"""
        output = run_compiler_with_stdin(spec_text)
        self.assertIn("RESULT 1: TRUE", output)
        self.assertIn("RESULT 2: TRUE", output)

    def test_separate_domain_and_query_fragments(self) -> None:
        domain_text = """initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5
"""
        first_output = krr_compiler.evaluate_text_fragments(
            domain_text,
            "doorOpen after openDoor\n",
        )
        second_output = krr_compiler.evaluate_text_fragments(
            domain_text,
            "openDoor executable with cost 5\n",
        )
        self.assertIn("RESULT 1: FALSE", first_output)
        self.assertIn("RESULT 1: TRUE", second_output)

    def test_split_spec_text_for_interactive_editor(self) -> None:
        domain_text, query_text = krr_compiler.split_spec_text_for_editor(
            """[domain]
initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5

[queries]
doorOpen after openDoor
openDoor executable with cost 5
""",
            "example.txt",
        )
        self.assertEqual(
            domain_text,
            "initially !doorOpen\nopenDoor causes doorOpen if hasKey\nopenDoor costs 5",
        )
        self.assertEqual(
            query_text,
            "doorOpen after openDoor\nopenDoor executable with cost 5",
        )

    def test_load_interactive_workspace_texts_from_spec_file(self) -> None:
        args = argparse.Namespace(
            spec_file=str(ROOT / "examples" / "example1.krr"),
            domain_file=None,
            query_file=None,
            interactive=True,
            show_models=False,
        )
        domain_text, query_text = krr_compiler.load_interactive_workspace_texts(args)
        self.assertIn("initially !doorOpen", domain_text)
        self.assertIn("openDoor causes doorOpen if hasKey", domain_text)
        self.assertIn("doorOpen after openDoor", query_text)
        self.assertIn("openDoor executable with cost 5", query_text)

    def test_load_editor_texts_from_spec_path(self) -> None:
        domain_text, query_text = krr_compiler.load_editor_texts_from_spec_path(
            ROOT / "examples" / "example1.krr"
        )
        self.assertIn("initially !doorOpen", domain_text)
        self.assertIn("openDoor costs 5", domain_text)
        self.assertIn("doorOpen after openDoor", query_text)

    def test_inconsistent_domain_hides_query_results(self) -> None:
        output = krr_compiler.evaluate_text_fragments(
            "initially !doorOpen\ninitially !hasKey\ndoorOpen after openDoor\nopenDoor causes doorOpen if hasKey\nopenDoor costs 5\n",
            "doorOpen after openDoor\nopenDoor executable with cost 5\n",
        )
        self.assertEqual(output, krr_compiler.INCONSISTENT_DOMAIN_MESSAGE)

    def test_contradictory_effects_are_reported_as_inconsistent_domain(self) -> None:
        output = krr_compiler.evaluate_text_fragments(
            "toggle causes on\ntoggle causes !on\n",
            "on after toggle\n",
        )
        self.assertEqual(output, krr_compiler.INCONSISTENT_DOMAIN_MESSAGE)

    def test_conflicting_costs_are_reported_as_inconsistent_domain(self) -> None:
        output = krr_compiler.evaluate_text_fragments(
            "flip causes on\nflip costs 1\nflip costs 2\n",
            "flip executable with cost 2\n",
        )
        self.assertEqual(output, krr_compiler.INCONSISTENT_DOMAIN_MESSAGE)

    def test_delete_selection_or_backspace_removes_selected_text(self) -> None:
        buffer = Buffer()
        buffer.text = "hello world"
        buffer.cursor_position = len(buffer.text)
        buffer.start_selection()
        buffer.cursor_position = 6

        krr_compiler.delete_selection_or_backspace(buffer)

        self.assertEqual(buffer.text, "hello ")

    def test_mouse_wrapper_clears_toolbar_hover_on_pane_entry(self) -> None:
        calls: list[str] = []

        def original_handler(mouse_event: MouseEvent) -> str:
            calls.append(mouse_event.event_type.value)
            return "handled"

        wrapped = krr_compiler.wrap_mouse_handler_to_clear_toolbar_hover(
            original_handler,
            clear_hover=lambda: calls.append("clear"),
        )

        result = wrapped(
            MouseEvent(
                position=Point(x=0, y=0),
                event_type=MouseEventType.MOUSE_MOVE,
                button=MouseButton.NONE,
                modifiers=frozenset(),
            )
        )

        self.assertEqual(result, "handled")
        self.assertEqual(calls, ["clear", MouseEventType.MOUSE_MOVE.value])

    def test_interactive_domain_errors_name_the_domain_window(self) -> None:
        with self.assertRaisesRegex(
            krr_compiler.ParseError,
            r"Domain window: Line 1: invalid action '1openDoor'\.",
        ):
            krr_compiler.evaluate_text_fragments(
                "1openDoor causes doorOpen\n",
                "doorOpen after openDoor\n",
            )

    def test_interactive_query_errors_name_the_queries_window(self) -> None:
        with self.assertRaisesRegex(
            krr_compiler.ParseError,
            r"Queries window: Line 1: query references unknown action 'unknownAction'\.",
        ):
            krr_compiler.evaluate_text_fragments(
                "initially !doorOpen\nopenDoor causes doorOpen if hasKey\nopenDoor costs 5\n",
                "unknownAction executable with cost 0\n",
            )


    def test_interactive_requires_nonempty_submission(self) -> None:
        completed = run_compiler_failure(["--interactive"], "")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Interactive input ended before the specification was submitted.", completed.stderr)

    def test_stdin_requires_domain_section(self) -> None:
        completed = run_compiler_failure([], "initially !doorOpen\n[queries]\ndoorOpen after openDoor\n")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("missing [domain] section", completed.stderr)

    def test_stdin_requires_queries_section(self) -> None:
        completed = run_compiler_failure([], "[domain]\ninitially !doorOpen\n")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("missing [queries] section", completed.stderr)


if __name__ == "__main__":
    unittest.main()
