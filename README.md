# KRR Project : Actions with Cost

This repository  includes a compiler/evaluator for the DS4 action-cost language
described in `theory.pdf`.

## Files

- `krr_compiler.py`: parser, model builder, query evaluator, and CLI
- `examples/*.krr`: input files matching the examples from `theory.tex`
- `tests/test_examples.py`: regression tests for the theory examples
- `build_exe.ps1`: PyInstaller build script for creating a Windows `.exe`
- `requirements.txt`: pinned dependency list for the local virtual environment

## Input format

The compiler accepts a combined spec file with `[domain]` and `[queries]` sections.
Statements are written in a direct ASCII form of the notation from the theory:

```text
[domain]
initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5

[queries]
doorOpen after openDoor
openDoor executable with cost 5
openDoor executable with exact cost 5
```

Notes:

- Negation is written as `!fluent`
- Programs are comma-separated action lists
- Literal lists are comma-separated, for example `doorOpen, hasKey after openDoor`
- Comments start with `#`

## Run

```powershell
python -m venv .venvb --without-pip
python -m pip install --target .venvb\Lib\site-packages -r requirements.txt
python krr_compiler.py examples/example1.krr
python -m unittest tests/test_examples.py
```

You can also run it without files and type the code directly from the keyboard:

```powershell
python krr_compiler.py --interactive
```

If your terminal supports it, interactive mode opens a multiline editor that behaves like one text box:

```text
code> [domain]
initially !doorOpen
openDoor causes doorOpen if hasKey
openDoor costs 5
[queries]
doorOpen after openDoor
openDoor executable with cost 5
```

Press `F5` or `Ctrl+R` to run the whole buffer.
Format:

```text
[domain]
... domain statements ...
[queries]
... query statements ...
```

If you want to pipe a full spec through standard input, use the same `[domain]` / `[queries]` format.

Interactive mode rules:

- You must include both `[domain]` and `[queries]`
- Pressing Enter continues the spec on the next line
- `F5` or `Ctrl+R` runs the full text buffer
- The compiler reports missing, duplicated, or misordered sections before evaluation

To list accepted initial completions:

```powershell
python krr_compiler.py examples/example1.krr --show-models
```

## Build `.exe`

If `PyInstaller` is installed:

```powershell
.\build_exe.ps1
```

The executable will be created in `dist\krr-action-cost-compiler-editor.exe`.
