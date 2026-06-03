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

You can also open an existing combined spec file in the interactive editor:

```powershell
python krr_compiler.py --interactive your_file.txt
```

Inside the interactive editor, you can press `Ctrl+O` to choose and open a `.krr` or `.txt` spec file from a file picker.
The opened file should use the combined structure:

```text
[domain]
... domain statements ...
[queries]
... query statements ...
```

If your terminal supports it, interactive mode opens a persistent split editor:

```text
[Open] [Run] [Help] [Exit]

---------------------------------------------
| Domain                      | Queries      |
| initially !doorOpen         | doorOpen ... |
| openDoor causes ...         | openDoor ... |
---------------------------------------------
| Output                                     |
---------------------------------------------
```

The left pane is only for domain statements and the right pane is only for query statements.
You do not need to type `[domain]` or `[queries]` inside the interactive editor.
The Output pane starts empty and only shows compiler results or errors after you run.

If you want to pipe a full spec through standard input, use the normal combined `[domain]` / `[queries]` format.

Interactive mode rules:

- Pressing Enter continues editing in the current pane
- `Tab` and `Shift+Tab` switch between the Domain and Queries panes
- `Ctrl+O` opens a combined `.krr` or `.txt` spec file into the editor
- `F5` or `Ctrl+R` runs the compiler without closing the editor
- `Esc` or `Ctrl+Q` exits the editor
- `F1` toggles the detailed help panel, and the panel can also be closed with its `X` button
- The top `Open`, `Run`, `Help`, and `Exit` labels are clickable buttons
- The `F1` help panel includes syntax descriptions and examples for both the Domain and Queries panes
- The domain stays in place, so you can change queries and run again as many times as you want
- If there is an error, it appears in the Output pane and you can keep editing both panes immediately

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
