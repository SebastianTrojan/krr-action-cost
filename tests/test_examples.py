from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import krr_compiler


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
        self.assertIn("DOMAIN STATUS: inconsistent", output)
        self.assertIn("RESULT 1: UNDEFINED", output)
        self.assertIn("RESULT 2: UNDEFINED", output)
        self.assertIn("RESULT 3: UNDEFINED", output)

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
